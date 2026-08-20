"""Tests for the fit_calibration service.

Tests cover:
- Funnel metrics computation (conversion rates)
- Keyword extraction from job postings
- Insight generation for various scenarios
- Edge cases (no outcomes, single outcome, all rejected)
"""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Application,
    Base,
    CandidateProfile,
    JobPosting,
    Outcome,
    RankEvaluation,
    User,
)
from app.exceptions import NotFoundError
from app.services.fit_calibration import (
    generate_calibration_report,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
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
    )
    db_session.add(candidate)
    await db_session.commit()
    await db_session.refresh(candidate)
    return candidate


async def _create_job(db, user_id, title, company, description, requirements=None):
    """Helper to create a job posting."""
    job = JobPosting(
        user_id=user_id,
        portal="linkedin",
        external_id=f"job-{company.lower().replace(' ', '-')}",
        title=title,
        company=company,
        location="Copenhagen",
        description=description,
        requirements=requirements or [],
        employment_type="full-time",
        language="en",
        status="ranked",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _create_evaluation(db, user_id, job_id, score=80, verdict="Good Fit", missing_keywords=None):
    """Helper to create a rank evaluation."""
    eval_ = RankEvaluation(
        job_posting_id=job_id,
        user_id=user_id,
        technical_score=score,
        experience_score=score,
        behavioral_score=score,
        career_score=score,
        overall_score=score,
        verdict=verdict,
        location_status="PASS",
        missing_keywords=missing_keywords or [],
        strengths=["Good fit"],
        gaps=["Minor gap"],
        red_flags=[],
        language="en",
    )
    db.add(eval_)
    await db.commit()
    await db.refresh(eval_)
    return eval_


async def _create_application(db, user_id, job_id, eval_id):
    """Helper to create an application."""
    app = Application(
        user_id=user_id,
        job_posting_id=job_id,
        rank_evaluation_id=eval_id,
        cv_template="moderncv-banking",
        cover_letter_template="cover-cls",
        language="en",
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return app


async def _create_outcome(db, user_id, app_id, status, date_resolved=None):
    """Helper to create an outcome."""
    outcome = Outcome(
        user_id=user_id,
        application_id=app_id,
        status=status,
        date_resolved=date_resolved,
    )
    db.add(outcome)
    await db.commit()
    await db.refresh(outcome)
    return outcome


# ── Tests: generate_calibration_report ──────────────────────────────


@pytest.mark.asyncio
async def test_no_outcomes_raises_not_found(db_session):
    """generate_calibration_report raises NotFoundError when no outcomes exist."""
    with pytest.raises(NotFoundError, match="No outcomes found"):
        await generate_calibration_report(db_session, "test-user-id")


@pytest.mark.asyncio
async def test_basic_funnel_with_one_outcome(db_session, sample_candidate):
    """Single hired outcome produces correct funnel metrics."""
    job = await _create_job(db_session, "test-user-id", "ML Engineer", "TechCorp", "Python PyTorch Kubernetes AWS")
    eval_ = await _create_evaluation(db_session, "test-user-id", job.id)
    app = await _create_application(db_session, "test-user-id", job.id, eval_.id)
    await _create_outcome(db_session, "test-user-id", app.id, "hired", "2026-08-01")

    report = await generate_calibration_report(db_session, "test-user-id")

    assert report.data_points == 1
    assert report.funnel.total_applications == 1
    assert report.funnel.interviews == 1  # hired implies interviewed
    assert report.funnel.offers == 1  # hired implies offer
    assert report.funnel.hired == 1
    assert report.funnel.application_to_interview_pct == 100.0
    assert report.funnel.offer_to_hired_pct == 100.0
    assert report.funnel.overall_success_pct == 100.0


@pytest.mark.asyncio
async def test_funnel_multiple_outcomes_mixed(db_session, sample_candidate):
    """Multiple outcomes produce correct conversion rates."""
    # Outcome 1: Rejected (no interview)
    job1 = await _create_job(db_session, "test-user-id", "ML Engineer", "CorpA", "Python", ["Python"])
    eval1 = await _create_evaluation(db_session, "test-user-id", job1.id, score=70)
    app1 = await _create_application(db_session, "test-user-id", job1.id, eval1.id)
    await _create_outcome(db_session, "test-user-id", app1.id, "rejected")

    # Outcome 2: Interviewed but rejected
    job2 = await _create_job(db_session, "test-user-id", "Data Scientist", "CorpB", "SQL R", ["SQL"])
    eval2 = await _create_evaluation(db_session, "test-user-id", job2.id, score=75)
    app2 = await _create_application(db_session, "test-user-id", job2.id, eval2.id)
    await _create_outcome(db_session, "test-user-id", app2.id, "interview_invited")

    # Outcome 3: Hired
    job3 = await _create_job(db_session, "test-user-id", "Senior ML", "CorpC", "PyTorch Kubernetes Docker", ["PyTorch"])
    eval3 = await _create_evaluation(db_session, "test-user-id", job3.id, score=90)
    app3 = await _create_application(db_session, "test-user-id", job3.id, eval3.id)
    await _create_outcome(db_session, "test-user-id", app3.id, "hired", "2026-08-01")

    report = await generate_calibration_report(db_session, "test-user-id")

    assert report.data_points == 3
    assert report.funnel.total_applications == 3
    # Only outcomes 2 and 3 had interviews
    assert report.funnel.interviews == 2
    # Only outcome 3 had an offer
    assert report.funnel.offers == 1
    assert report.funnel.hired == 1
    assert report.funnel.rejected == 1
    assert report.funnel.application_to_interview_pct == pytest.approx(66.7, rel=0.1)
    assert report.funnel.interview_to_offer_pct == 50.0
    assert report.funnel.offer_to_hired_pct == 100.0
    assert report.funnel.overall_success_pct == pytest.approx(33.3, rel=0.1)


@pytest.mark.asyncio
async def test_funnel_all_rejected(db_session, sample_candidate):
    """All outcomes rejected produces 0% conversion rates."""
    for i in range(3):
        job = await _create_job(db_session, "test-user-id", f"Job {i}", f"Corp{i}", "skills", ["skill"])
        eval_ = await _create_evaluation(db_session, "test-user-id", job.id, score=60)
        app = await _create_application(db_session, "test-user-id", job.id, eval_.id)
        await _create_outcome(db_session, "test-user-id", app.id, "rejected")

    report = await generate_calibration_report(db_session, "test-user-id")

    assert report.funnel.total_applications == 3
    assert report.funnel.interviews == 0
    assert report.funnel.offers == 0
    assert report.funnel.hired == 0
    assert report.funnel.rejected == 3
    assert report.funnel.application_to_interview_pct == 0.0
    assert report.funnel.overall_success_pct == 0.0


# ── Tests: keyword analysis ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_keyword_analysis_correlation(db_session, sample_candidate):
    """Keywords from successful jobs appear in top_keywords."""
    # Create 2 successful outcomes (hired) with Python keyword
    for i in range(2):
        job = await _create_job(
            db_session, "test-user-id", f"ML Engineer {i}", f"GoodCorp{i}", "Python PyTorch ML", ["Python", "PyTorch"]
        )
        eval_ = await _create_evaluation(db_session, "test-user-id", job.id, score=90)
        app = await _create_application(db_session, "test-user-id", job.id, eval_.id)
        await _create_outcome(db_session, "test-user-id", app.id, "hired")

    # Create 2 unsuccessful outcomes with Java keyword
    for i in range(2):
        job = await _create_job(
            db_session, "test-user-id", f"Java Dev {i}", f"BadCorp{i}", "Java Spring", ["Java", "Spring"]
        )
        eval_ = await _create_evaluation(db_session, "test-user-id", job.id, score=50)
        app = await _create_application(db_session, "test-user-id", job.id, eval_.id)
        await _create_outcome(db_session, "test-user-id", app.id, "rejected")

    report = await generate_calibration_report(db_session, "test-user-id")

    assert len(report.top_keywords) > 0
    # Python should correlate positively (in both hired jobs)
    python_kw = next((k for k in report.top_keywords if k.keyword == "python"), None)
    assert python_kw is not None
    assert python_kw.interview_rate == 100.0
    assert python_kw.correlation == "positive"

    # Java should correlate negatively (in both rejected jobs)
    java_kw = next((k for k in report.bottom_keywords if k.keyword == "java"), None)
    assert java_kw is not None
    assert java_kw.interview_rate == 0.0


# ── Tests: insights ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insights_generated_for_multiple_outcomes(db_session, sample_candidate):
    """Insights are generated when enough data exists."""
    # Create 5 outcomes: 2 hired, 2 rejected, 1 in progress
    for i in range(5):
        job = await _create_job(db_session, "test-user-id", f"Job {i}", f"Corp{i}", "skills", ["skill"])
        eval_ = await _create_evaluation(db_session, "test-user-id", job.id, score=70)
        app = await _create_application(db_session, "test-user-id", job.id, eval_.id)
        status = "hired" if i < 2 else ("rejected" if i < 4 else "interview_invited")
        await _create_outcome(db_session, "test-user-id", app.id, status)

    report = await generate_calibration_report(db_session, "test-user-id")

    # Should have insights (>0)
    assert len(report.insights) > 0
    # Total apps >= 5 triggers keyword insights
    assert report.data_points == 5
    assert report.funnel.total_applications == 5


@pytest.mark.asyncio
async def test_insights_low_interview_rate(db_session, sample_candidate):
    """Low interview rate triggers funnel insight."""
    # 5 applications, 0 interviews → low rate insight
    for i in range(5):
        job = await _create_job(db_session, "test-user-id", f"Job {i}", f"Corp{i}", "skills", ["skill"])
        eval_ = await _create_evaluation(db_session, "test-user-id", job.id, score=60)
        app = await _create_application(db_session, "test-user-id", job.id, eval_.id)
        await _create_outcome(db_session, "test-user-id", app.id, "rejected")

    report = await generate_calibration_report(db_session, "test-user-id")

    # Should have a funnel insight about low interview rate
    funnel_insights = [i for i in report.insights if i.category == "funnel"]
    assert len(funnel_insights) > 0
    assert any("low interview rate" in i.insight.lower() for i in funnel_insights)


# ── Tests: edge cases ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_keywords_from_empty_job_description(db_session, sample_candidate):
    """Job posting with no description produces no keywords (no crash)."""
    job = await _create_job(db_session, "test-user-id", "Some Job", "SomeCorp", "", [])
    eval_ = await _create_evaluation(db_session, "test-user-id", job.id, score=80)
    app = await _create_application(db_session, "test-user-id", job.id, eval_.id)
    await _create_outcome(db_session, "test-user-id", app.id, "hired")

    report = await generate_calibration_report(db_session, "test-user-id")
    # Should not crash — top/bottom keywords will be empty
    assert report.data_points == 1
    assert report.funnel.hired == 1


@pytest.mark.asyncio
async def test_funnel_latest_outcome_only(db_session, sample_candidate):
    """When an app has multiple outcomes, only the latest is used for funnel."""
    job = await _create_job(db_session, "test-user-id", "Job", "Corp", "Python", ["Python"])
    eval_ = await _create_evaluation(db_session, "test-user-id", job.id, score=80)
    app = await _create_application(db_session, "test-user-id", job.id, eval_.id)

    # Same app: first invited, then hired
    await _create_outcome(db_session, "test-user-id", app.id, "interview_invited")
    await _create_outcome(db_session, "test-user-id", app.id, "hired")

    report = await generate_calibration_report(db_session, "test-user-id")

    # Only 1 application (deduplicated)
    assert report.funnel.total_applications == 1
    # Should count as hired (latest status)
    assert report.funnel.hired == 1
    assert report.funnel.interviews == 1


@pytest.mark.asyncio
async def test_report_includes_generated_at_timestamp(db_session, sample_candidate):
    """CalibrationReport includes a valid generated_at timestamp."""
    job = await _create_job(db_session, "test-user-id", "Job", "Corp", "skills", ["skill"])
    eval_ = await _create_evaluation(db_session, "test-user-id", job.id, score=80)
    app = await _create_application(db_session, "test-user-id", job.id, eval_.id)
    await _create_outcome(db_session, "test-user-id", app.id, "hired")

    report = await generate_calibration_report(db_session, "test-user-id")
    assert report.generated_at is not None
    assert isinstance(report.generated_at, datetime)
