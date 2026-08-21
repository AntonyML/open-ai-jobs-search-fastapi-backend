"""Tests for the CV generator service (FASE 1).

Uses an in-memory SQLite database and mocks the LLM + Typst compile steps
so no network or typst binary is required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import AppNotification, Base, CandidateProfile, GeneratedCV, JobPosting, User
from app.exceptions import (
    NotFoundError,
    PreconditionError,
    ProfileIncompleteError,
    ProviderAuthError,
    WebSearchUnavailableError,
)
from app.services import cv_generator

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """In-memory SQLite DB with a test user and candidate profile."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        # Expose the factory so tests of compile_cv_in_background can open a
        # second session over the same in-memory engine.
        session._test_factory = session_factory
        session.add(
            User(
                id="test-user-id",
                email="test@example.com",
                hashed_password="fakehash",
                full_name="Test User",
            )
        )
        session.add(
            CandidateProfile(
                user_id="test-user-id",
                full_name="Jane Doe",
                location="Copenhagen, Denmark",
                email="jane@example.com",
                constraints="No relocation",
                education=[{"degree": "MSc Computer Science", "institution": "DTU", "period": "2018-2020"}],
                experience=[
                    {
                        "title": "Software Engineer",
                        "company": "Acme",
                        "start_date": "2020-01",
                        "end_date": "Present",
                        "bullets": ["Built X to improve Y"],
                    }
                ],
                skills={"programming_ml": [{"language": "Python", "proficiency": "advanced"}]},
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


SAMPLE_OUTPUT = {
    "cv": {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "experience": [
            {
                "title": "Software Engineer",
                "company": "Acme",
                "date_range": {"start": "2020-01", "end": "Present"},
                "bullets": ["Built X to improve Y"],
            }
        ],
        "profile_statement": "Results-driven engineer.",
    },
    "metadata": {"language": "en"},
}

SAMPLE_ANALYSIS = {
    "match_score": 78,
    "missing_keywords": ["Kubernetes"],
    "red_flags": ["Few senior years"],
    "adapted_experience": ["Lead the X bullet with scale"],
}


# ── Tests ───────────────────────────────────────────────────────────


PROVIDER_CFG = {"provider": "openai", "model": "gpt-4o", "api_key": "sk-test", "api_base": None}


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch("app.services.cv_generator.generate_base_cv_llm", new=AsyncMock(return_value=SAMPLE_OUTPUT))
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_generate_base_cv_persists(db_session):
    record = await cv_generator.generate_base_cv(db_session, "test-user-id")

    assert record.id
    assert record.cv_type == "base"
    assert record.user_id == "test-user-id"
    assert record.cv_json["cv"]["profile_statement"]
    assert record.analysis is None
    assert record.job_description_text is None
    # PDF compilation is async (CAPA 4): the record returns without a path;
    # compile_cv_in_background() records it once Typst finishes.
    assert record.pdf_path is None


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.personalize_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_personalize_cv_persists_analysis(db_session):
    record = await cv_generator.personalize_cv(db_session, "test-user-id", "Senior Python Engineer at Acme..." * 3)

    assert record.cv_type == "personalized"
    assert record.analysis["match_score"] == 78
    assert "Kubernetes" in record.analysis["missing_keywords"]
    assert record.job_description_text.startswith("Senior Python")


@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value={"provider": None, "model": None, "api_key": None, "api_base": None}),
)
async def test_generate_base_cv_without_provider_raises(db_session):
    with pytest.raises(ProviderAuthError):
        # global provider config is empty -> ProviderAuthError
        await cv_generator.generate_base_cv(db_session, "test-user-id")


async def test_generate_without_profile_raises(db_session):
    with pytest.raises(ProfileIncompleteError):
        await cv_generator.generate_base_cv(db_session, "no-profile-user")


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.personalize_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_list_get_soft_delete_and_rate_count(db_session):
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")
    second = await cv_generator.personalize_cv(
        db_session, "test-user-id", "Backend Engineer role requiring Go and SQL..." * 2
    )

    listed = await cv_generator.list_cvs(db_session, "test-user-id")
    assert {c.id for c in listed} == {first.id, second.id}

    fetched = await cv_generator.get_cv(db_session, "test-user-id", first.id)
    assert fetched.id == first.id

    recent = await cv_generator.count_recent_cvs(db_session, "test-user-id", window_minutes=60)
    assert recent == 2

    await cv_generator.soft_delete_cv(db_session, "test-user-id", first.id)
    assert [c.id for c in await cv_generator.list_cvs(db_session, "test-user-id")] == [second.id]

    with pytest.raises(NotFoundError):
        await cv_generator.get_cv(db_session, "test-user-id", first.id)

    with pytest.raises(NotFoundError):
        await cv_generator.get_cv(db_session, "other-user", second.id)


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.adapt_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_adapt_cv_requires_base_cv(db_session):
    """Rule 4 — adapting without a base CV raises PreconditionError."""
    with pytest.raises(PreconditionError):
        await cv_generator.adapt_cv(db_session, "test-user-id", "missing-base-cv", "missing-job")


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.adapt_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_adapt_cv_persists_new_document_without_touching_base(db_session):
    """Rules 5-7 — adapted CV links to the job, base CV record is untouched."""
    # 1. Create the base CV
    base = await cv_generator.generate_base_cv(db_session, "test-user-id")
    base_cv_json_before = base.cv_json

    # 2. Create a job posting owned by the user
    job = JobPosting(
        id="job-adapt-1",
        user_id="test-user-id",
        portal="linkedin",
        external_id="ext-1",
        title="Senior Python Engineer",
        company="Acme",
        location="Remote",
        description="Python, FastAPI, Kubernetes...",
        requirements=["Python", "Kubernetes"],
        status="ranked",
    )
    db_session.add(job)
    await db_session.commit()

    # 3. Adapt
    adapted = await cv_generator.adapt_cv(db_session, "test-user-id", base.id, "job-adapt-1")

    assert adapted.cv_type == "personalized"
    assert adapted.job_posting_id == "job-adapt-1"
    assert adapted.job_url is None
    assert adapted.analysis["match_score"] == 78
    assert adapted.id != base.id

    # Rule 6 — base CV untouched
    await db_session.refresh(base)
    assert base.cv_json == base_cv_json_before
    assert base.cv_type == "base"

    # Rule 7 — list shows both documents
    listed = await cv_generator.list_cvs(db_session, "test-user-id")
    assert {c.id for c in listed} == {base.id, adapted.id}


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.adapt_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_adapt_cv_job_not_owned_raises(db_session):
    """Job posting must belong to the user (404)."""
    base = await cv_generator.generate_base_cv(db_session, "test-user-id")

    other_job = JobPosting(
        id="job-other-1",
        user_id="someone-else",
        portal="linkedin",
        external_id="ext-2",
        title="Backend Engineer",
        company="OtherCo",
        location="NYC",
        description="Go and SQL...",
        status="ranked",
    )
    db_session.add(other_job)
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await cv_generator.adapt_cv(db_session, "test-user-id", base.id, "job-other-1")


# ── Async PDF compile (CAPA 4) ────────────────────────────────────────


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
async def test_compile_cv_in_background_sets_pdf_path(db_session):
    """The background task compiles the PDF and records the path on the row."""
    record = GeneratedCV(
        id="cv-async-1",
        user_id="test-user-id",
        cv_type="base",
        base_status="active",
        cv_json=SAMPLE_OUTPUT,
    )
    db_session.add(record)
    await db_session.commit()

    # Mock file read to return valid PDF bytes (min 100 bytes for validation)
    with patch("pathlib.Path.read_bytes", return_value=b"%PDF-1.4" + b" " * 100):
        await cv_generator.compile_cv_in_background(
            "cv-async-1",
            "test-user-id",
            SAMPLE_OUTPUT,
            session_factory=db_session._test_factory,
        )

    await db_session.refresh(record)
    assert record.pdf_path and record.pdf_path.endswith(".pdf")


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
async def test_compile_cv_in_background_skips_deleted_record(db_session):
    """If the CV was deleted while compiling, no path is written back."""
    record = GeneratedCV(
        id="cv-async-2",
        user_id="test-user-id",
        cv_type="personalized",
        cv_json=SAMPLE_OUTPUT,
        is_deleted=True,
    )
    db_session.add(record)
    await db_session.commit()

    # Mock file read to return valid PDF bytes (min 100 bytes for validation)
    with patch("pathlib.Path.read_bytes", return_value=b"%PDF-1.4" + b" " * 100):
        # Must not raise, and must not resurrect the deleted row's pdf_path.
        await cv_generator.compile_cv_in_background(
            "cv-async-2",
            "test-user-id",
            SAMPLE_OUTPUT,
            session_factory=db_session._test_factory,
        )

    await db_session.refresh(record)
    assert record.pdf_path is None


@patch("app.services.cv_generator.compile_cv", new=MagicMock(side_effect=RuntimeError("typst boom")))
async def test_compile_cv_in_background_failure_is_non_fatal(db_session):
    """A failed compile keeps the record (pdf_path stays None) and notifies the admin."""
    admin = User(
        id="admin-user-id",
        email="admin@example.com",
        hashed_password="fakehash",
        full_name="Admin",
        role="admin",
    )
    db_session.add(admin)
    record = GeneratedCV(
        id="cv-async-3",
        user_id="test-user-id",
        cv_type="base",
        base_status="active",
        cv_json=SAMPLE_OUTPUT,
    )
    db_session.add(record)
    await db_session.commit()

    await cv_generator.compile_cv_in_background(
        "cv-async-3",
        "test-user-id",
        SAMPLE_OUTPUT,
        session_factory=db_session._test_factory,
    )

    await db_session.refresh(record)
    assert record.pdf_path is None

    result = await db_session.execute(select(AppNotification).where(AppNotification.user_id == "admin-user-id"))
    notes = list(result.scalars().all())
    assert len(notes) == 1
    assert notes[0].type == "cv_pdf_compile_failed"


# ── Max-2 base CV lifecycle (active / obsolete) ────────────────────────


async def _bases(db) -> list[GeneratedCV]:
    return await cv_generator._user_base_cvs(db, "test-user-id")


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_generate_base_cv_keeps_at_most_two(db_session):
    """Regenerating never leaves more than 1 active + 1 obsolete base CV."""
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")
    assert first.base_status == "active"

    second = await cv_generator.generate_base_cv(db_session, "test-user-id")
    await db_session.refresh(first)
    assert first.base_status == "obsolete"
    assert second.base_status == "active"

    third = await cv_generator.generate_base_cv(db_session, "test-user-id")
    await db_session.refresh(second)
    assert second.base_status == "obsolete"
    assert third.base_status == "active"

    bases = await _bases(db_session)
    assert len(bases) == 2  # the oldest (first) was hard-deleted
    assert {b.id for b in bases} == {second.id, third.id}
    assert sum(1 for b in bases if b.base_status == "active") == 1
    assert sum(1 for b in bases if b.base_status == "obsolete") == 1


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_recover_previous_base_swaps_roles(db_session):
    """Recover swaps: previous → active, current active → obsolete."""
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")
    second = await cv_generator.generate_base_cv(db_session, "test-user-id")
    await db_session.refresh(first)

    restored = await cv_generator.recover_previous_base(db_session, "test-user-id", first.id)

    assert restored.id == first.id
    assert restored.base_status == "active"
    await db_session.refresh(second)
    assert second.base_status == "obsolete"

    # The invariant still holds and no third document exists.
    bases = await _bases(db_session)
    assert len(bases) == 2
    assert sum(1 for b in bases if b.base_status == "active") == 1


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_recover_previous_base_rejects_active_or_foreign(db_session):
    """Only an owned, obsolete base CV can be restored."""
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")

    # Active base is not recoverable.
    with pytest.raises(PreconditionError):
        await cv_generator.recover_previous_base(db_session, "test-user-id", first.id)

    # A CV belonging to another user is not recoverable.
    with pytest.raises(PreconditionError):
        await cv_generator.recover_previous_base(db_session, "test-user-id", "someone-elses-cv")


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
@patch("app.services.r2_storage._r2_configured", new=MagicMock(return_value=False))
async def test_soft_delete_obsolete_base_hard_deletes_pdf(db_session):
    """Deleting the obsolete base removes the row AND the PDF from disk."""
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")
    second = await cv_generator.generate_base_cv(db_session, "test-user-id")
    await db_session.refresh(first)
    assert first.base_status == "obsolete"

    # Compilation is async now — simulate a record whose background task
    # already finished by recording its pdf_path (the compiled file on disk).
    first.pdf_path = f"generated_cvs/{first.user_id}/{first.id}.pdf"
    await db_session.commit()
    pdf = cv_generator.resolve_pdf_path(first)
    assert pdf is not None
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 test")

    await cv_generator.soft_delete_cv(db_session, "test-user-id", first.id)

    assert not pdf.exists(), "PDF of the obsolete base should be removed from disk"
    bases = await _bases(db_session)
    assert [b.id for b in bases] == [second.id]
    await db_session.refresh(second)
    assert second.base_status == "active"


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_soft_delete_active_base_promotes_previous(db_session):
    """Deleting the active base promotes the previous one to active."""
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")
    second = await cv_generator.generate_base_cv(db_session, "test-user-id")
    await db_session.refresh(first)
    assert first.base_status == "obsolete"

    await cv_generator.soft_delete_cv(db_session, "test-user-id", second.id)

    await db_session.refresh(first)
    assert first.base_status == "active"
    bases = await _bases(db_session)
    assert [b.id for b in bases] == [first.id]


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.personalize_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
@patch("app.services.r2_storage._r2_configured", new=MagicMock(return_value=False))
async def test_soft_delete_personalized_removes_pdf(db_session):
    """Soft-deleting a personalized CV removes its PDF, marks the row, idempotently.

    Fase 1a: deleting a personalized CV must free the PDF on disk immediately
    (it is a derived artifact re-compilable from ``cv_json``), not accumulate
    orphaned files under ``generated_cvs/``.
    """
    record = await cv_generator.personalize_cv(db_session, "test-user-id", "Senior Python Engineer at Acme..." * 3)
    assert record.cv_type == "personalized"

    # Compilation is async now — simulate a finished background task by
    # recording the pdf_path (the compiled file on disk).
    record.pdf_path = f"generated_cvs/{record.user_id}/{record.id}.pdf"
    await db_session.commit()
    pdf = cv_generator.resolve_pdf_path(record)
    assert pdf is not None
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 test")

    await cv_generator.soft_delete_cv(db_session, "test-user-id", record.id)

    assert not pdf.exists(), "PDF of a soft-deleted personalized CV should be removed from disk"
    await db_session.refresh(record)
    assert record.is_deleted is True
    assert record.deleted_at is not None

    # Idempotent: the file is already gone, calling again must not raise.
    cv_generator._remove_pdf_file(record)


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.adapt_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_adapt_cv_requires_active_base(db_session):
    """Rule 4 — only the ACTIVE base CV can be adapted, not an obsolete one."""
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")
    second = await cv_generator.generate_base_cv(db_session, "test-user-id")
    await db_session.refresh(first)
    assert first.base_status == "obsolete"

    job = JobPosting(
        id="job-active-1",
        user_id="test-user-id",
        portal="linkedin",
        external_id="ext-active",
        title="Senior Python Engineer",
        company="Acme",
        location="Remote",
        description="Python, FastAPI, Kubernetes...",
        status="ranked",
    )
    db_session.add(job)
    await db_session.commit()

    # Obsolete base → precondition error.
    with pytest.raises(PreconditionError):
        await cv_generator.adapt_cv(db_session, "test-user-id", first.id, "job-active-1")

    # Active base still adapts fine.
    await cv_generator.adapt_cv(db_session, "test-user-id", second.id, "job-active-1")


# ── Adapt by URL (all plans — the model reads the link, no scraping) ──


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.adapt_cv_llm_with_url",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_adapt_cv_from_url_persists_with_source(db_session):
    """Adapting by URL stores a new personalized CV with job_url (no fetched text)."""
    base = await cv_generator.generate_base_cv(db_session, "test-user-id")

    adapted = await cv_generator.adapt_cv_from_url(
        db_session,
        "test-user-id",
        base.id,
        "https://www.linkedin.com/jobs/view/4415693439",
    )

    assert adapted.cv_type == "personalized"
    assert adapted.job_url == "https://www.linkedin.com/jobs/view/4415693439"
    assert adapted.job_posting_id is None
    assert adapted.job_description_text is None  # we never fetch the page
    assert adapted.analysis["match_score"] == 78
    assert adapted.id != base.id

    # Rule 6 — base CV untouched
    await db_session.refresh(base)
    assert base.cv_type == "base"
    assert base.base_status == "active"

    listed = await cv_generator.list_cvs(db_session, "test-user-id")
    assert {c.id for c in listed} == {base.id, adapted.id}


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.adapt_cv_llm_with_url",
    new=AsyncMock(
        side_effect=WebSearchUnavailableError("The configured AI model can't open links. Use a model with web search.")
    ),
)
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_adapt_cv_from_url_propagates_no_web_search_error_and_notifies_admin(db_session):
    """A model without web access surfaces the error, persists nothing and
    notifies the admin so the provider config gets fixed."""
    admin = User(
        id="admin-user-id",
        email="admin@example.com",
        hashed_password="fakehash",
        full_name="Admin",
        role="admin",
    )
    db_session.add(admin)
    await db_session.commit()

    base = await cv_generator.generate_base_cv(db_session, "test-user-id")

    with pytest.raises(WebSearchUnavailableError, match="web search"):
        await cv_generator.adapt_cv_from_url(db_session, "test-user-id", base.id, "https://www.example.com/job")

    # No personalized CV was persisted.
    listed = await cv_generator.list_cvs(db_session, "test-user-id")
    assert [c.id for c in listed] == [base.id]

    # The admin received an in-app notification about the config issue.
    result = await db_session.execute(select(AppNotification).where(AppNotification.user_id == "admin-user-id"))
    notes = list(result.scalars().all())
    assert len(notes) == 1
    assert notes[0].type == "provider_config_issue"
    assert notes[0].payload["user_id"] == "test-user-id"
    assert notes[0].payload["url"] == "https://www.example.com/job"


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.adapt_cv_llm_with_url",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.get_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_adapt_cv_from_url_requires_active_base(db_session):
    """Rule 4 — only the ACTIVE base CV can be adapted by URL."""
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")
    await cv_generator.generate_base_cv(db_session, "test-user-id")
    await db_session.refresh(first)
    assert first.base_status == "obsolete"

    with pytest.raises(PreconditionError):
        await cv_generator.adapt_cv_from_url(db_session, "test-user-id", first.id, "https://www.example.com/job")
