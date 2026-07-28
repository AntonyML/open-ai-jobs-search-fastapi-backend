"""Tests for rank_jobs.start() — especially the IngestedJob → JobPosting adapter.

Covers C1–C6, C10, C11 from the verification checklist.
Uses SQLite in-memory and patches the orchestrator queue.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Base,
    CandidateProfile,
    ExecutionJob,
    ExecutionJobItem,
    IngestedJob,
    JobPosting,
    User,
)
from app.services.rank_jobs import start


class _SessionFactory:
    """Reusable session wrapper — same pattern as existing tests."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args) -> None:
        pass


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        user = User(
            id="test-user-id",
            email="test@example.com",
            hashed_password="fakehash",
            full_name="Test User",
        )
        session.add(user)
        candidate = CandidateProfile(
            user_id="test-user-id",
            location="Copenhagen, Denmark",
            skills={
                "programming_ml": [{"language": "Python", "proficiency": "Expert"}],
                "domain_expertise": ["ML"],
            },
            experience=[{"title": "ML Engineer", "company": "Acme", "start_date": "2020-01", "end_date": "Present"}],
        )
        session.add(candidate)
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
def db_factory(db_session):
    return _SessionFactory(db_session)


_call_count = 0

@pytest.fixture
def mock_queue():
    """Patch the ExecutionQueue to avoid real DB orchestration.
    Returns unique job_id per call.
    """
    global _call_count
    with patch("app.services.rank_jobs._get_queue") as mock:
        queue = MagicMock()

        async def _enqueue(**kwargs):
            global _call_count
            _call_count += 1
            return (f"exec-job-{_call_count:04d}", MagicMock())

        queue.enqueue = _enqueue
        mock.return_value = queue
        yield queue


# ── Helpers ──────────────────────────────────────────────────────────


async def seed_ingested_jobs(db: AsyncSession, count: int = 3) -> list[IngestedJob]:
    """Create test IngestedJob records with all fields populated."""
    jobs = []
    for i in range(count):
        j = IngestedJob(
            id=f"ij-{i:08d}-{i}",
            title=f"ML Engineer {i}",
            company=f"TechCorp {i}",
            location="Copenhagen, Denmark",
            url=f"https://example.com/job/{i}",
            description=f"We need an ML Engineer with {i}+ years of experience. Salary negotiable.",
            salary=f"{80 + i * 10}k-{100 + i * 10}k DKK",
            portal="telegram",
            category_id="stem_cr",
            source_channel="test_channel",
            source_message_id=i,
            raw_text=f"ML Engineer {i} at TechCorp {i}",
            ingested_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        )
        db.add(j)
        jobs.append(j)
    await db.commit()
    for j in jobs:
        await db.refresh(j)
    return jobs


# ═══════════════════════════════════════════════════════════════════════
# C4: Adapter field preservation (no silent data loss)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_adapter_preserves_all_fields(db_session, db_factory, mock_queue):
    """C4: IngestedJob → JobPosting adapter preserves every field the RankAnalyzer consumes.

    Critical: salary must NOT be lost in the conversion.
    """
    ingested = await seed_ingested_jobs(db_session, 1)
    ij = ingested[0]

    result = await start(db_factory, "test-user-id", {"job_ids": [ij.id]})

    assert result["status"] == "queued", f"Expected queued, got {result}"
    assert result["accepted_jobs"] == 1, f"Expected 1 accepted job"

    # Read back the JobPosting that was created
    jp = await db_session.get(JobPosting, ij.id)
    assert jp is not None, "JobPosting was not created"

    # Check every mapped field
    assert jp.id == ij.id, "id mismatch"
    assert jp.title == ij.title, f"title mismatch: {jp.title} != {ij.title}"
    assert jp.company == ij.company, f"company mismatch: {jp.company} != {ij.company}"
    assert jp.location == ij.location, f"location mismatch: {jp.location} != {ij.location}"
    assert jp.url == ij.url, f"url mismatch: {jp.url} != {ij.url}"
    assert jp.description == ij.description, f"description mismatch (truncated?)"
    assert jp.salary == ij.salary, f"salary LOST: {jp.salary} != {ij.salary}"
    assert jp.portal == (ij.portal or "web"), f"portal mismatch"
    assert jp.status == "new", f"status should be 'new', got {jp.status}"
    assert jp.user_id == "test-user-id", f"user_id mismatch"


@pytest.mark.asyncio
async def test_adapter_preserves_all_fields_multiple(db_session, db_factory, mock_queue):
    """C4: Multiple jobs — every field preserved for every record."""
    ingested = await seed_ingested_jobs(db_session, 3)
    ids = [j.id for j in ingested]

    result = await start(db_factory, "test-user-id", {"job_ids": ids})
    assert result["accepted_jobs"] == 3

    for ij in ingested:
        jp = await db_session.get(JobPosting, ij.id)
        assert jp is not None, f"JobPosting missing for {ij.id}"
        assert jp.salary == ij.salary, f"salary lost for {ij.id}"
        assert jp.description == ij.description, f"description lost for {ij.id}"


@pytest.mark.asyncio
async def test_adapter_handles_null_fields(db_session, db_factory, mock_queue):
    """C4: Nullable fields (salary, description, location) can be None without crash."""
    j = IngestedJob(
        id="ij-null-fields-001",
        title="Null Job",
        company=None,
        location=None,
        url=None,
        description=None,
        salary=None,
        portal=None,
        category_id="stem_cr",
        source_channel="test",
        source_message_id=1,
        raw_text="test",
        ingested_at=datetime.now(timezone.utc),
    )
    db_session.add(j)
    await db_session.commit()

    result = await start(db_factory, "test-user-id", {"job_ids": [j.id]})
    assert result["accepted_jobs"] == 1

    jp = await db_session.get(JobPosting, j.id)
    assert jp is not None
    assert jp.title == "Null Job"
    assert jp.salary is None
    assert jp.description is None
    assert jp.company is None
    assert jp.location is None


# ═══════════════════════════════════════════════════════════════════════
# C1: job_ids selects exactly those IDs (no more, no less)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_job_ids_selects_exactly_those(db_session, db_factory, mock_queue):
    """C1: When job_ids provided, start() creates exactly those ExecutionJobItems."""
    ingested = await seed_ingested_jobs(db_session, 5)
    selected_ids = [ingested[0].id, ingested[2].id, ingested[4].id]

    result = await start(db_factory, "test-user-id", {"job_ids": selected_ids})
    exec_job_id = result["job_id"]

    # Verify ExecutionJobItem records
    items = (
        await db_session.execute(
            select(ExecutionJobItem).where(ExecutionJobItem.execution_job_id == exec_job_id)
        )
    ).scalars().all()

    assert len(items) == 3, f"Expected 3 items, got {len(items)}"
    item_job_ids = {i.job_posting_id for i in items}
    assert item_job_ids == set(selected_ids), f"Item job IDs {item_job_ids} != selected {selected_ids}"

    # Verify total_jobs and accepted_jobs in response
    assert result["total_jobs"] == 3
    assert result["accepted_jobs"] == 3


# ═══════════════════════════════════════════════════════════════════════
# C2: Empty / null job_ids behavior
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_job_ids_none_falls_back_to_jobposting(db_session, db_factory, mock_queue):
    """C2: When job_ids is None, start() falls back to JobPosting query."""
    # Create a JobPosting directly (not via adapter)
    jp = JobPosting(
        id="jp-direct-001",
        user_id="test-user-id",
        portal="linkedin",
        external_id="ext-001",
        title="Direct Job",
        company="Corp",
        status="new",
    )
    db_session.add(jp)
    await db_session.commit()

    result = await start(db_factory, "test-user-id", {})
    assert result["status"] == "queued"
    assert result["total_jobs"] == 1


@pytest.mark.asyncio
async def test_job_ids_empty_list_returns_message(db_session, db_factory, mock_queue):
    """C2: Empty job_ids list returns 'No ingested jobs found' — not a crash."""
    result = await start(db_factory, "test-user-id", {"job_ids": []})
    assert result["status"] == "skipped"
    assert "No ingested jobs found" in result.get("message", "")


# ═══════════════════════════════════════════════════════════════════════
# C5: Nonexistent / expired IDs handled gracefully
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_nonexistent_job_ids_returns_message(db_session, db_factory, mock_queue):
    """C5: Nonexistent IDs don't crash — return message."""
    result = await start(db_factory, "test-user-id", {"job_ids": ["nonexistent-1", "nonexistent-2"]})
    assert result["status"] == "skipped"
    assert "No ingested jobs found" in result.get("message", "")


@pytest.mark.asyncio
async def test_mixed_existing_and_nonexistent_ids(db_session, db_factory, mock_queue):
    """C5: Only existing IDs are ranked; nonexistent are silently skipped."""
    ingested = await seed_ingested_jobs(db_session, 2)
    real_ids = [ingested[0].id]
    mixed = real_ids + ["fake-id-1", "fake-id-2"]

    result = await start(db_factory, "test-user-id", {"job_ids": mixed})
    assert result["accepted_jobs"] == 1
    assert result["total_jobs"] == 1


# ═══════════════════════════════════════════════════════════════════════
# C6: User selection integrity — the full round trip
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_user_selects_exact_count(db_session, db_factory, mock_queue):
    """C6: User selects 2 of 5 — only 2 are ranked. The heart of the experience."""
    ingested = await seed_ingested_jobs(db_session, 5)
    # User selects jobs 0 and 4 (not 1, 2, 3)
    selection = [ingested[0].id, ingested[4].id]

    result = await start(db_factory, "test-user-id", {"job_ids": selection})
    assert result["accepted_jobs"] == 2
    assert result["total_jobs"] == 2

    exec_job_id = result["job_id"]
    items = (
        await db_session.execute(
            select(ExecutionJobItem).where(ExecutionJobItem.execution_job_id == exec_job_id)
        )
    ).scalars().all()
    assert len(items) == 2
    ranked_ids = {i.job_posting_id for i in items}
    assert ranked_ids == set(selection), f"Ranked {ranked_ids} != selected {set(selection)}"


# ═══════════════════════════════════════════════════════════════════════
# C10: Worker propagation — items are enqueued with correct references
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_worker_items_have_correct_references(db_session, db_factory, mock_queue):
    """C10: ExecutionJobItem records reference the correct ExecutionJob and IngestedJob IDs."""
    ingested = await seed_ingested_jobs(db_session, 2)
    ids = [j.id for j in ingested]

    result = await start(db_factory, "test-user-id", {"job_ids": ids})
    exec_job_id = result["job_id"]

    items = (
        await db_session.execute(
            select(ExecutionJobItem)
            .where(ExecutionJobItem.execution_job_id == exec_job_id)
            .order_by(ExecutionJobItem.job_posting_id)
        )
    ).scalars().all()

    assert len(items) == 2
    for item, ij_id in zip(items, sorted(ids)):
        assert item.execution_job_id == exec_job_id
        assert item.job_posting_id == ij_id
        assert item.user_id == "test-user-id"
        assert item.status == "queued"


@pytest.mark.asyncio
async def test_worker_items_claimable_with_for_update(db_session, db_factory, mock_queue):
    """C10: Items enqueued from job_ids are claimable via FOR UPDATE SKIP LOCKED."""
    ingested = await seed_ingested_jobs(db_session, 1)
    result = await start(db_factory, "test-user-id", {"job_ids": [ingested[0].id]})
    exec_job_id = result["job_id"]

    async with db_factory() as db:
        item = (
            await db.execute(
                select(ExecutionJobItem)
                .where(ExecutionJobItem.execution_job_id == exec_job_id)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()

    assert item is not None, "Worker should be able to claim the item"
    assert item.job_posting_id == ingested[0].id


# ═══════════════════════════════════════════════════════════════════════
# C11: Idempotency — same job_ids doesn't create duplicates
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_idempotency_same_key_returns_existing(db_session, db_factory, mock_queue):
    """C11: Same idempotency_key returns existing job.

    We create the ExecutionJob + set idempotency_key in DB directly since
    the mock queue doesn't persist it.
    """
    ingested = await seed_ingested_jobs(db_session, 1)
    ij = ingested[0]

    # Create the ExecutionJob + Item as start() would
    exec_job = ExecutionJob(
        id="exec-idemp-001",
        user_id="test-user-id",
        pipeline="rank",
        status="queued",
        description="Test rank",
        provider="test",
        model="test",
        max_retries=1,
        idempotency_key="rank-key-1",
    )
    db_session.add(exec_job)
    item = ExecutionJobItem(
        execution_job_id=exec_job.id,
        job_posting_id=ij.id,
        user_id="test-user-id",
        status="queued",
    )
    db_session.add(item)
    await db_session.commit()

    # Call with same key — should return existing
    result = await start(
        db_factory, "test-user-id",
        {"job_ids": [ij.id]},
        idempotency_key="rank-key-1",
    )
    # With the key hitting in DB, start() returns before calling enqueue,
    # so mock_queue.enqueue is not called again.
    assert result["job_id"] == exec_job.id
    assert result["status"] == "queued"


@pytest.mark.asyncio
async def test_different_idempotency_keys_different_jobs(db_session, db_factory, mock_queue):
    """C11: Different keys create different jobs."""
    ingested = await seed_ingested_jobs(db_session, 1)

    r1 = await start(db_factory, "test-user-id", {"job_ids": [ingested[0].id]}, idempotency_key="key-a")
    r2 = await start(db_factory, "test-user-id", {"job_ids": [ingested[0].id]}, idempotency_key="key-b")
    assert r2["job_id"] != r1["job_id"]


# ═══════════════════════════════════════════════════════════════════════
# Edge: Re-rank doesn't recreate ingested jobs
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_job_ids_rerank_skips_existing_jobposting(db_session, db_factory, mock_queue):
    """If JobPosting already exists (from a prior rank), start() reuses it without error."""
    ingested = await seed_ingested_jobs(db_session, 1)
    ij = ingested[0]

    # Create JobPosting already (simulates a previous rank run)
    existing_jp = JobPosting(
        id=ij.id,
        user_id="test-user-id",
        portal="telegram",
        external_id=f"ij_{ij.id}",
        title=ij.title,
        company=ij.company,
        salary=ij.salary,
        status="ranked",
    )
    db_session.add(existing_jp)
    await db_session.commit()

    # Re-rank with same ID
    result = await start(db_factory, "test-user-id", {"job_ids": [ij.id]})
    assert result["accepted_jobs"] == 1

    # Verify only one JobPosting exists (no duplicate)
    all_jp = (await db_session.execute(select(JobPosting))).scalars().all()
    assert len(all_jp) == 1
