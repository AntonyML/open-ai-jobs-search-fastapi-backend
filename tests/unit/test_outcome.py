"""Tests for the outcome service.

Uses an in-memory SQLite database and mocks the CSV operations.
"""

import asyncio
import csv
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Application,
    CandidateProfile,
    JobPosting,
    Outcome,
    RankEvaluation,
    User,
)
from app.exceptions import NotFoundError
from app.services import outcome


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        from app.db.models import Base
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        # Create a test user
        user = User(
            id="test-user-id",
            email="test@example.com",
            hashed_password="fakehash",
            full_name="Test User",
        )
        session.add(user)
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
async def sample_candidate(db_session):
    """Create a sample candidate profile."""
    candidate = CandidateProfile(
        user_id="test-user-id",
        full_name="Jane Doe",
        location="Copenhagen, Denmark",
        email="jane@example.com",
        phone="+45 12345678",
        linkedin_url="https://linkedin.com/in/janedoe",
        github_url="https://github.com/janedoe",
        employment_status="Employed",
        constraints="No relocation",
        education=[
            {"degree": "MSc Computer Science", "institution": "DTU", "period": "2018-2020", "key_topics": "ML, Distributed Systems"}
        ],
        experience=[
            {
                "title": "Senior ML Engineer",
                "company": "Acme Corp",
                "start_date": "2020-01",
                "end_date": "Present",
                "location": "Copenhagen",
                "bullets": [
                    "Built ML pipeline processing 1M+ events/day",
                    "Reduced model latency by 40% via TensorRT optimization",
                    "Led team of 3 engineers",
                ],
            },
            {
                "title": "Data Scientist",
                "company": "Beta Inc",
                "start_date": "2018-06",
                "end_date": "2019-12",
                "location": "Aarhus",
                "bullets": [
                    "Developed recommendation system",
                    "Published 2 papers at top conferences",
                ],
            },
        ],
        projects=[
            {"name": "Open Source ML Library", "description": "Contributor to popular ML library"}
        ],
        skills={
            "programming_ml": [
                {"language": "Python", "proficiency": "Expert", "frameworks": ["PyTorch", "TensorFlow", "scikit-learn"]},
                {"language": "SQL", "proficiency": "Advanced", "frameworks": []},
            ],
            "domain_expertise": ["Machine Learning", "NLP", "Recommendation Systems"],
            "software_tools": ["Docker", "Kubernetes", "AWS", "Git"],
        },
        publications=[
            {"authors": "Doe, J.", "year": "2021", "title": "Efficient Transformers", "journal": "NeurIPS", "doi": "10.xxxx/xxxx"}
        ],
        awards=[
            {"award": "Best Paper Award", "event": "ICML", "year": "2020"}
        ],
        references=[
            {"name": "John Smith", "title": "CTO", "company": "Acme Corp", "email": "john@acme.com"}
        ],
        profile_statement="ML engineer with 5+ years building production ML systems at scale.",
    )
    db_session.add(candidate)
    await db_session.commit()
    await db_session.refresh(candidate)
    return candidate


@pytest.fixture
async def sample_job(db_session, sample_candidate):
    """Create a sample job posting."""
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-123",
        title="Senior Machine Learning Engineer",
        company="TechCorp",
        location="Copenhagen, Denmark",
        url="https://linkedin.com/jobs/123",
        posting_date="2026-07-10",
        deadline="2026-08-10",
        description="We are looking for a Senior ML Engineer to build scalable ML systems. Experience with PyTorch, Kubernetes, and AWS required. You will lead a team of 3-5 engineers.",
        requirements=[
            "5+ years ML engineering experience",
            "Expert in Python and PyTorch",
            "Experience with Kubernetes and AWS",
            "Team leadership experience",
            "Strong communication skills",
        ],
        employment_type="full-time",
        language="en",
        status="ranked",
        rank_score=83.0,
        rank_verdict="Strong Fit",
        rank_date=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest.fixture
async def sample_evaluation(db_session, sample_candidate, sample_job):
    """Create a sample rank evaluation."""
    evaluation = RankEvaluation(
        job_posting_id=sample_job.id,
        user_id="test-user-id",
        technical_score=85,
        experience_score=80,
        behavioral_score=75,
        career_score=90,
        overall_score=83,
        verdict="Strong Fit",
        location_status="PASS",
        deadline="2026-08-10",
        deadline_urgent=False,
        strengths=["Strong ML engineering background", "Team leadership experience", "Production ML at scale"],
        gaps=["No explicit Kubernetes certification", "Limited public cloud architecture experience"],
        missing_keywords=["Kubernetes", "AWS", "CI/CD"],
        red_flags=["Gap in employment 2017-2018"],
        language="en",
        raw_response={},
    )
    db_session.add(evaluation)
    await db_session.commit()
    await db_session.refresh(evaluation)
    return evaluation


@pytest.fixture
async def sample_application(db_session, sample_candidate, sample_job, sample_evaluation):
    """Create a sample application."""
    application = Application(
        user_id="test-user-id",
        job_posting_id=sample_job.id,
        rank_evaluation_id=sample_evaluation.id,
        tailored_experience=[
            {
                "title": "Senior ML Engineer",
                "company": "Acme Corp",
                "start_date": "2020-01",
                "end_date": "Present",
                "location": "Copenhagen",
                "bullets": [
                    "Accomplished 40% reduction in model inference latency, as measured by p99 latency, by implementing TensorRT optimization and batching",
                    "Achieved processing of 1M+ events/day, measured by throughput metrics, by building scalable ML pipeline with PyTorch and Kubernetes",
                    "Led team of 5 engineers to deliver real-time fraud detection system processing 10K transactions/sec with <50ms latency",
                ],
            },
            {
                "title": "Data Scientist",
                "company": "Beta Inc",
                "start_date": "2018-06",
                "end_date": "2019-12",
                "location": "Aarhus",
                "bullets": [
                    "Increased recommendation click-through rate by 15%, measured via A/B test, by adding collaborative filtering signals to ranking model",
                    "Published 2 papers at top conferences, demonstrating research impact, by conducting novel NLP research",
                ],
            },
        ],
        incorporated_keywords=[
            {"keyword": "Kubernetes", "where_incorporated": "Senior ML Engineer at Acme Corp, bullet 2", "original_context": "Required in job posting: Kubernetes"},
            {"keyword": "AWS", "where_incorporated": "Senior ML Engineer at Acme Corp, bullet 2", "original_context": "Required in job posting: AWS"},
        ],
        addressed_red_flags=["Gap in employment 2017-2018"],
        cv_pdf_path="/tmp/cv.pdf",
        cover_letter_pdf_path="/tmp/cover.pdf",
        cv_compiled=True,
        cv_pages=2,
        cover_letter_compiled=True,
        cover_letter_pages=1,
        cv_template="moderncv-banking",
        cover_letter_template="cover-cls",
        language="en",
    )
    db_session.add(application)
    await db_session.commit()
    await db_session.refresh(application)
    return application


# ── Helper: mock CSV operations ─────────────────────────────────────


def mock_tracker_csv():
    """Create a mock tracker CSV file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=outcome._get_tracker_fieldnames())
        writer.writeheader()
        writer.writerow({
            "date": "2026-07-10",
            "company": "TechCorp",
            "sector": "Technology",
            "role": "Senior Machine Learning Engineer",
            "role_type": "full-time",
            "channel": "linkedin",
            "status": "applied",
            "contact_person": "",
            "fit_rating": "",
            "notes": "",
            "cv_file": "/tmp/cv.pdf",
            "cover_letter_file": "/tmp/cover.pdf",
            "source": "https://linkedin.com/jobs/123",
        })
        return Path(f.name)


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_outcome_basic(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """execute_outcome records a new outcome and updates job status."""
    with patch("app.services.outcome._get_tracker_path") as mock_tracker_path:
        mock_tracker_path.return_value = mock_tracker_csv()

        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                outcome_result = await outcome.execute_outcome(
                    db=db_session,
                    user_id="test-user-id",
                    payload=outcome.OutcomeCreate(
                        application_id=sample_application.id,
                        status="interview_invited",
                        phone_screen_date="2026-07-15",
                        notes="Phone screen scheduled with hiring manager",
                    ),
                )

    assert outcome_result.id is not None
    assert outcome_result.application_id == sample_application.id
    assert outcome_result.status == "interview_invited"
    assert outcome_result.phone_screen_date == "2026-07-15"
    assert outcome_result.notes == "Phone screen scheduled with hiring manager"

    # Check job status was updated
    await db_session.refresh(sample_job)
    assert sample_job.status == "interview"


@pytest.mark.asyncio
async def test_execute_outcome_resolution(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """execute_outcome with a resolution status sets date_resolved."""
    with patch("app.services.outcome._get_tracker_path") as mock_tracker_path:
        mock_tracker_path.return_value = mock_tracker_csv()

        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                outcome_result = await outcome.execute_outcome(
                    db=db_session,
                    user_id="test-user-id",
                    payload=outcome.OutcomeCreate(
                        application_id=sample_application.id,
                        status="hired",
                        date_resolved="2026-08-01",
                        notes="Accepted offer! Starting September.",
                        lessons_learned="Tailoring CV to job keywords made a huge difference.",
                        valued_signals=["Tailored CV", "STAR examples", "Company research"],
                    ),
                )

    assert outcome_result.status == "hired"
    assert outcome_result.date_resolved == "2026-08-01"
    assert outcome_result.notes == "Accepted offer! Starting September."
    assert outcome_result.lessons_learned == "Tailoring CV to job keywords made a huge difference."
    assert outcome_result.valued_signals == ["Tailored CV", "STAR examples", "Company research"]

    # Check job status was updated to hired
    await db_session.refresh(sample_job)
    assert sample_job.status == "hired"


@pytest.mark.asyncio
async def test_execute_outcome_application_not_found(db_session):
    """execute_outcome raises NotFoundError for non-existent application."""
    with pytest.raises(NotFoundError):
        await outcome.execute_outcome(
            db=db_session,
            user_id="test-user-id",
            payload=outcome.OutcomeCreate(
                application_id="nonexistent-id",
                status="interview_invited",
            ),
        )


@pytest.mark.asyncio
async def test_execute_outcome_wrong_user(db_session, sample_application):
    """execute_outcome raises NotFoundError when application belongs to another user."""
    with pytest.raises(NotFoundError):
        await outcome.execute_outcome(
            db=db_session,
            user_id="other-user-id",
            payload=outcome.OutcomeCreate(
                application_id=sample_application.id,
                status="interview_invited",
            ),
        )


@pytest.mark.asyncio
async def test_execute_outcome_updates_tracker_csv(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """execute_outcome updates job_search_tracker.csv."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=outcome._get_tracker_fieldnames())
        writer.writeheader()
        tracker_path = Path(f.name)

    with patch("app.services.outcome._get_tracker_path", return_value=tracker_path):
        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                await outcome.execute_outcome(
                    db=db_session,
                    user_id="test-user-id",
                    payload=outcome.OutcomeCreate(
                        application_id=sample_application.id,
                        status="hired",
                        date_resolved="2026-08-01",
                    ),
                )

    # Read tracker and verify
    with open(tracker_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 1
    assert rows[0]["company"] == "TechCorp"
    assert rows[0]["role"] == "Senior Machine Learning Engineer"
    assert rows[0]["status"] == "hired"


@pytest.mark.asyncio
async def test_execute_outcome_archives_outcome_md(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """execute_outcome archives outcome.md in documents/applications/."""
    with patch("app.services.outcome._get_tracker_path") as mock_tracker_path:
        mock_tracker_path.return_value = mock_tracker_csv()

        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                await outcome.execute_outcome(
                    db=db_session,
                    user_id="test-user-id",
                    payload=outcome.OutcomeCreate(
                        application_id=sample_application.id,
                        status="hired",
                        date_resolved="2026-08-01",
                        notes="Accepted offer!",
                        lessons_learned="Tailoring CV worked.",
                        valued_signals=["Tailored CV", "STAR examples"],
                    ),
                )

                # Check outcome.md was created (inside the temp dir context)
                apps_dir = Path(tmpdir)
                company_slug = "techcorp"
                role_slug = "senior_machine_learning_engineer"
                app_dir = apps_dir / f"{company_slug}_{role_slug}"
                outcome_md_path = app_dir / "outcome.md"

                assert outcome_md_path.exists()
                content = outcome_md_path.read_text(encoding="utf-8")
                assert "Outcome: TechCorp — Senior Machine Learning Engineer" in content
                assert "**Status:** hired" in content
                assert "**Date resolved:** 2026-08-01" in content
                assert "Accepted offer!" in content
                assert "Tailoring CV worked." in content
                assert "Tailored CV" in content
                assert "STAR examples" in content


@pytest.mark.asyncio
async def test_get_outcome(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """get_outcome returns the outcome by ID."""
    with patch("app.services.outcome._get_tracker_path") as mock_tracker_path:
        mock_tracker_path.return_value = mock_tracker_csv()

        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                created = await outcome.execute_outcome(
                    db=db_session,
                    user_id="test-user-id",
                    payload=outcome.OutcomeCreate(
                        application_id=sample_application.id,
                        status="hired",
                        date_resolved="2026-08-01",
                    ),
                )

    fetched = await outcome.get_outcome(db_session, created.id, "test-user-id")
    assert fetched.id == created.id
    assert fetched.status == "hired"


@pytest.mark.asyncio
async def test_get_outcome_not_found(db_session):
    """get_outcome raises NotFoundError for non-existent outcome."""
    with pytest.raises(NotFoundError):
        await outcome.get_outcome(db_session, "nonexistent-id", "test-user-id")


@pytest.mark.asyncio
async def test_get_outcome_wrong_user(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """get_outcome raises NotFoundError when outcome belongs to another user."""
    with patch("app.services.outcome._get_tracker_path") as mock_tracker_path:
        mock_tracker_path.return_value = mock_tracker_csv()

        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                created = await outcome.execute_outcome(
                    db=db_session,
                    user_id="test-user-id",
                    payload=outcome.OutcomeCreate(
                        application_id=sample_application.id,
                        status="hired",
                        date_resolved="2026-08-01",
                    ),
                )

    with pytest.raises(NotFoundError):
        await outcome.get_outcome(db_session, created.id, "other-user-id")


@pytest.mark.asyncio
async def test_list_outcomes(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """list_outcomes returns outcomes for the user."""
    with patch("app.services.outcome._get_tracker_path") as mock_tracker_path:
        mock_tracker_path.return_value = mock_tracker_csv()

        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                # Create 3 outcomes
                for i in range(3):
                    await outcome.execute_outcome(
                        db=db_session,
                        user_id="test-user-id",
                        payload=outcome.OutcomeCreate(
                            application_id=sample_application.id,
                            status="hired" if i == 0 else "rejected",
                            date_resolved="2026-08-01",
                        ),
                    )

    outcomes = await outcome.list_outcomes(db_session, "test-user-id", limit=10)
    assert len(outcomes) == 3


@pytest.mark.asyncio
async def test_list_tracker_rows(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """list_tracker_rows returns rows from job_search_tracker.csv."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=outcome._get_tracker_fieldnames())
        writer.writeheader()
        writer.writerow({
            "date": "2026-07-10",
            "company": "TechCorp",
            "sector": "Technology",
            "role": "Senior Machine Learning Engineer",
            "role_type": "full-time",
            "channel": "linkedin",
            "status": "hired",
            "contact_person": "",
            "fit_rating": "",
            "notes": "Accepted offer",
            "cv_file": "/tmp/cv.pdf",
            "cover_letter_file": "/tmp/cover.pdf",
            "source": "https://linkedin.com/jobs/123",
        })
        tracker_path = Path(f.name)

    with patch("app.services.outcome._get_tracker_path", return_value=tracker_path):
        rows = await outcome.list_tracker_rows(db_session, "test-user-id")

    assert len(rows) == 1
    assert rows[0].company == "TechCorp"
    assert rows[0].role == "Senior Machine Learning Engineer"
    assert rows[0].status == "hired"


@pytest.mark.asyncio
async def test_update_outcome(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """update_outcome updates an existing outcome."""
    with patch("app.services.outcome._get_tracker_path") as mock_tracker_path:
        mock_tracker_path.return_value = mock_tracker_csv()

        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                created = await outcome.execute_outcome(
                    db=db_session,
                    user_id="test-user-id",
                    payload=outcome.OutcomeCreate(
                        application_id=sample_application.id,
                        status="interview_invited",
                        phone_screen_date="2026-07-15",
                    ),
                )

    # Update the outcome
    updated = await outcome.update_outcome(
        db=db_session,
        user_id="test-user-id",
        outcome_id=created.id,
        payload=outcome.OutcomeUpdate(
            status="phone_screen_completed",
            phone_screen_date="2026-07-15",
            technical_date="2026-07-20",
            notes="Phone screen went well, technical interview scheduled",
        ),
    )

    assert updated.status == "phone_screen_completed"
    assert updated.technical_date == "2026-07-20"
    assert updated.notes == "Phone screen went well, technical interview scheduled"


@pytest.mark.asyncio
async def test_update_outcome_not_found(db_session):
    """update_outcome raises NotFoundError for non-existent outcome."""
    with pytest.raises(NotFoundError):
        await outcome.update_outcome(
            db=db_session,
            user_id="test-user-id",
            outcome_id="nonexistent-id",
            payload=outcome.OutcomeUpdate(status="hired"),
        )


@pytest.mark.asyncio
async def test_update_outcome_wrong_user(db_session, sample_candidate, sample_job, sample_application, sample_evaluation):
    """update_outcome raises NotFoundError when outcome belongs to another user."""
    with patch("app.services.outcome._get_tracker_path") as mock_tracker_path:
        mock_tracker_path.return_value = mock_tracker_csv()

        with patch("app.services.outcome._get_applications_dir") as mock_apps_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_apps_dir.return_value = Path(tmpdir)

                created = await outcome.execute_outcome(
                    db=db_session,
                    user_id="test-user-id",
                    payload=outcome.OutcomeCreate(
                        application_id=sample_application.id,
                        status="interview_invited",
                    ),
                    )

    with pytest.raises(NotFoundError):
        await outcome.update_outcome(
            db=db_session,
            user_id="other-user-id",
            outcome_id=created.id,
            payload=outcome.OutcomeUpdate(status="hired"),
        )


# ── Schema validation tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_outcome_create_schema():
    """OutcomeCreate schema validates required fields."""
    payload = outcome.OutcomeCreate(
        application_id="app-123",
        status="interview_invited",
        phone_screen_date="2026-07-15",
    )
    assert payload.application_id == "app-123"
    assert payload.status == "interview_invited"
    assert payload.phone_screen_date == "2026-07-15"


@pytest.mark.asyncio
async def test_outcome_update_schema():
    """OutcomeUpdate schema allows partial updates."""
    payload = outcome.OutcomeUpdate(
        status="hired",
        date_resolved="2026-08-01",
    )
    assert payload.status == "hired"
    assert payload.date_resolved == "2026-08-01"
    assert payload.notes is None


# ── Tracker fieldnames test ─────────────────────────────────────────


def test_tracker_fieldnames():
    """_get_tracker_fieldnames returns correct fieldnames."""
    fieldnames = outcome._get_tracker_fieldnames()
    expected = [
        "date", "company", "sector", "role", "role_type", "channel",
        "status", "contact_person", "fit_rating", "notes",
        "cv_file", "cover_letter_file", "source",
    ]
    assert fieldnames == expected