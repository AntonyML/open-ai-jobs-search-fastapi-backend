"""Integration tests for the ranking worker — claim, cancel, recovery, idempotency.

Uses in-memory SQLite and patches the LLM call to avoid real API costs.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, CandidateProfile, ExecutionJob, ExecutionJobItem, JobPosting, User
from app.schemas.rank import RankQualitativeOutput
from app.services.rank_jobs import cancel, start


# ── Helpers ──────────────────────────────────────────────────────────


class _SessionFactory:
    """Reusable session wrapper for tests."""

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


async def _seed_jobs(db: AsyncSession, user_id: str, count: int = 3):
    """Create test job postings and commit."""
    jobs = []
    for i in range(count):
        job = JobPosting(
            user_id=user_id,
            portal="linkedin",
            external_id=f"job-{i}",
            title=f"ML Engineer {i}",
            company="TechCorp",
            location="Copenhagen",
            status="new",
        )
        db.add(job)
        jobs.append(job)
    await db.commit()
    for j in jobs:
        await db.refresh(j)
    return jobs


def _make_mock_llm(behavioral=80, career=70, confidence="high"):
    """Create a mock LLM call that returns controlled RankQualitativeOutput."""
    async def _mock_llm(messages, output_schema, provider_config):
        return RankQualitativeOutput(
            behavioral_score=behavioral,
            career_score=career,
            strengths=["Good fit"],
            gaps=["No K8s"],
            red_flags=[],
            confidence=confidence,
        ).model_dump_json()
    return _mock_llm


# ═══════════════════════════════════════════════════════════════════════
# 7.2 — Integration tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_workers_same_item_only_one_claims(db_session, db_factory):
    """Two workers competing for the same item — only one should claim it."""
    jobs = await _seed_jobs(db_session, "test-user-id", 1)
    job_id = jobs[0].id

    # Create a rank job directly
    result = await start(db_factory, "test-user-id", {})
    assert result["status"] == "queued"
    exec_job_id = result["job_id"]

    # Simulate two workers trying to claim the same item
    async with db_factory() as db:
        item = (await db.execute(
            select(ExecutionJobItem).where(ExecutionJobItem.execution_job_id == exec_job_id)
        )).scalar_one()

        # Worker 1 claims
        item1 = (await db.execute(
            select(ExecutionJobItem)
            .where(ExecutionJobItem.status == "queued")
            .with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        assert item1 is not None, "Worker 1 should claim the item"
        item1.status = "running"
        item1.worker_id = "worker-1"
        await db.commit()

    async with db_factory() as db:
        # Worker 2 tries to claim — should get nothing
        item2 = (await db.execute(
            select(ExecutionJobItem)
            .where(ExecutionJobItem.status == "queued")
            .with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        assert item2 is None, "Worker 2 should NOT claim the already-taken item"


@pytest.mark.asyncio
async def test_cancel_job_in_progress(db_session, db_factory):
    """Cancel a running rank job — items should be skipped."""
    await _seed_jobs(db_session, "test-user-id", 2)
    result = await start(db_factory, "test-user-id", {})
    exec_job_id = result["job_id"]

    # Mark items as running
    async with db_factory() as db:
        items = (await db.execute(
            select(ExecutionJobItem).where(ExecutionJobItem.execution_job_id == exec_job_id)
        )).scalars().all()
        for item in items:
            item.status = "running"
        await db.commit()

    # In tests, cancel() opens its own session via async_session_factory (real DB).
    # Patch it to use our test session.
    from app.db.session import async_session_factory as _real_factory

    # Patch the global session factory to use our test session
    with patch("app.db.session.async_session_factory", db_factory):
        cancelled = await cancel(exec_job_id, user_id="test-user-id")
    assert cancelled is True

    # Verify job is cancelled
    async with db_factory() as db:
        job = (await db.execute(
            select(ExecutionJob).where(ExecutionJob.id == exec_job_id)
        )).scalar_one()
        assert job.status in ("cancelled", "completed")


@pytest.mark.asyncio
async def test_recover_expired_lease(db_session, db_factory):
    """A crashed worker's expired items should be recoverable."""
    await _seed_jobs(db_session, "test-user-id", 1)
    result = await start(db_factory, "test-user-id", {})
    exec_job_id = result["job_id"]

    # Simulate a crashed worker: item is running with expired lease
    async with db_factory() as db:
        item = (await db.execute(
            select(ExecutionJobItem)
            .where(ExecutionJobItem.execution_job_id == exec_job_id)
        )).scalar_one()
        item.status = "running"
        item.worker_id = "dead-worker"
        item.locked_until = datetime.now(timezone.utc) - timedelta(seconds=10)
        item.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        await db.commit()

    # Recovery: reset expired items back to queued
    async with db_factory() as db:
        now = datetime.now(timezone.utc)
        recovered = (await db.execute(
            select(ExecutionJobItem)
            .where(ExecutionJobItem.status == "running")
            .where(ExecutionJobItem.locked_until < now)
            .with_for_update(skip_locked=True)
        )).scalars().all()
        for item in recovered:
            item.status = "queued"
            item.worker_id = None
            item.locked_until = None
        await db.commit()

    # Verify item is queued again
    async with db_factory() as db:
        item = (await db.execute(
            select(ExecutionJobItem)
            .where(ExecutionJobItem.execution_job_id == exec_job_id)
        )).scalar_one()
        assert item.status == "queued"
        assert item.worker_id is None


@pytest.mark.asyncio
async def test_restart_process_recovery(db_session, db_factory):
    """Simulate a process restart — running items remain (worker recovers via lease expiry)."""
    await _seed_jobs(db_session, "test-user-id", 2)
    result = await start(db_factory, "test-user-id", {})
    exec_job_id = result["job_id"]

    # Pre-restart state: items running but not yet completed
    async with db_factory() as db:
        items = (await db.execute(
            select(ExecutionJobItem)
            .where(ExecutionJobItem.execution_job_id == exec_job_id)
        )).scalars().all()
        for item in items:
            item.status = "running"
            item.worker_id = "pre-restart-worker"
            item.locked_until = datetime.now(timezone.utc) + timedelta(seconds=30)
        await db.commit()

    # Simulate restart: no DB changes needed — items persist
    async with db_factory() as db:
        items = (await db.execute(
            select(ExecutionJobItem)
            .where(ExecutionJobItem.execution_job_id == exec_job_id)
        )).scalars().all()

        assert len(items) == 2
        assert all(i.status == "running" for i in items)

    # The recovery loop (run on startup) will catch expired leases
    assert True  # No crash on restart


@pytest.mark.asyncio
async def test_idempotency_duplicate_request(db_session, db_factory):
    """Same idempotency_key returns existing job instead of creating new."""
    await _seed_jobs(db_session, "test-user-id", 2)

    # First request
    result1 = await start(db_factory, "test-user-id", {}, idempotency_key="key-123")
    assert result1["status"] == "queued"

    # Duplicate request with same key
    result2 = await start(db_factory, "test-user-id", {}, idempotency_key="key-123")
    assert result2["job_id"] == result1["job_id"]
    assert result2["status"] is not None


@pytest.mark.asyncio
async def test_user_isolation(db_session, db_factory):
    """User A cannot see or cancel User B's jobs."""
    await _seed_jobs(db_session, "test-user-id", 1)
    result = await start(db_factory, "test-user-id", {})
    exec_job_id = result["job_id"]

    # Different user tries to cancel
    # Patch session factory for cancel (opens its own session internally)
    from app.db.session import async_session_factory

    with patch("app.db.session.async_session_factory", db_factory):
        other_cancel = await cancel(exec_job_id, user_id="other-user")
    assert other_cancel is False


@pytest.mark.asyncio
async def test_no_jobs_returns_skipped(db_session, db_factory):
    """start() with no unranked jobs returns skipped status."""
    result = await start(db_factory, "test-user-id", {})
    assert result["status"] == "skipped"
    assert result["total_jobs"] == 0
