"""Tests for the rank service.

Uses an in-memory SQLite database and mocks the LLM calls.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, CandidateProfile, JobPosting, RankEvaluation, User
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.schemas.rank import RankLLMOutput
from app.services import rank


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
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
                    "Reduced model latency by 40%",
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
        status="new",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


# ── Helper: mock LLM ────────────────────────────────────────────────


def mock_orchestrator_output(
    behavioral=75,
    career=90,
    strengths=None,
    gaps=None,
    red_flags=None,
):
    """Create a mock RankLLMOutput for the orchestrator.

    Note: technical_score, experience_score, location_status, deadline,
    missing_keywords, and language are now computed deterministically
    by rank_analyzer, so we only mock the qualitative LLM fields.
    """
    return RankLLMOutput(
        technical_score=0,  # Will be overridden by deterministic analysis
        experience_score=0,  # Will be overridden by deterministic analysis
        behavioral_score=behavioral,
        career_score=career,
        location_status="PASS",  # Will be overridden
        deadline=None,  # Will be overridden
        deadline_urgent=False,  # Will be overridden
        strengths=strengths or ["Strong ML engineering background", "Team leadership experience", "Production ML at scale"],
        gaps=gaps or ["No explicit Kubernetes certification", "Limited public cloud architecture experience"],
        missing_keywords=[],  # Will be overridden
        red_flags=red_flags or ["Gap in employment 2017-2018"],
        language="en",  # Will be overridden
    )


# Mock the orchestrator to avoid creating DB sessions for the queue
@pytest.fixture
def mock_orchestrator():
    """Patch the orchestrator to return controlled LLM output.

    This avoids needing the orchestrator's DB tables (execution_jobs, etc.)
    while still testing the rank service logic.
    """
    with patch(
        "app.services.orchestrator.llm_orchestrator.LLMOrchestrator.execute"
    ) as mock:
        mock.return_value = mock_orchestrator_output()
        yield mock


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_rank_basic(db_session, sample_candidate, sample_job, mock_orchestrator):
    """execute_rank evaluates a job and returns a shortlist."""
    mock_orchestrator.return_value = mock_orchestrator_output(
        behavioral=75, career=90,
    )

    result = await rank.execute_rank(
        db=db_session,
        user_id="test-user-id",
        focus_area=None,
        re_rank=False,
        top_n=5,
    )

    assert result.ranked_count == 1
    assert len(result.shortlist) == 1
    assert result.below_threshold == 0
    assert result.expired_or_vetoed == 0

    # Check the shortlist item
    item = result.shortlist[0]
    assert item.job.title == "Senior Machine Learning Engineer"
    # Scores now come from deterministic analyzer, not mock
    assert isinstance(item.evaluation.technical_score, int)
    assert isinstance(item.evaluation.experience_score, int)
    assert item.evaluation.behavioral_score == 75  # From LLM mock
    assert item.evaluation.career_score == 90       # From LLM mock
    assert item.evaluation.verdict in ("Strong Fit", "Good Fit", "Moderate Fit", "Weak Fit", "Poor Fit")
    assert item.evaluation.location_status in ("PASS", "FAIL", "FLAG")

    # Check job was updated
    await db_session.refresh(sample_job)
    assert sample_job.status == "ranked"
    assert sample_job.rank_score is not None
    assert sample_job.rank_verdict is not None


@pytest.mark.asyncio
async def test_execute_rank_no_jobs(db_session, sample_candidate):
    """execute_rank returns empty result when no jobs to rank."""
    result = await rank.execute_rank(
        db=db_session,
        user_id="test-user-id",
        focus_area=None,
        re_rank=False,
        top_n=5,
    )

    assert result.ranked_count == 0
    assert result.shortlist == []
    assert result.message == "No new jobs to rank."


@pytest.mark.asyncio
async def test_execute_rank_profile_incomplete(db_session):
    """execute_rank raises ProfileIncompleteError when profile missing."""
    with pytest.raises(ProfileIncompleteError):
        await rank.execute_rank(
            db=db_session,
            user_id="test-user-id",
            focus_area=None,
            re_rank=False,
            top_n=5,
        )


@pytest.mark.asyncio
async def test_execute_rank_llm_error_continues(db_session, sample_candidate, sample_job):
    """execute_rank continues with other jobs if one LLM call fails."""
    # Add a second job
    job2 = JobPosting(
        user_id="test-user-id",
        portal="jobindex",
        external_id="job-456",
        title="Data Scientist",
        company="DataCorp",
        location="Aarhus",
        url="https://jobindex.dk/456",
        posting_date="2026-07-10",
        description="Data science role",
        status="new",
    )
    db_session.add(job2)
    await db_session.commit()

    with patch(
        "app.services.orchestrator.llm_orchestrator.LLMOrchestrator.execute"
    ) as mock:
        # First call succeeds, second fails
        mock.side_effect = [
            mock_orchestrator_output(),
            LLMError("LLM timeout"),
        ]

        result = await rank.execute_rank(
            db=db_session,
            user_id="test-user-id",
            focus_area=None,
            re_rank=False,
            top_n=5,
        )        # Should have ranked 1 job (the one that succeeded)
        # NOTE: shortlist may be empty if deterministic analyzer flags location
        # as FAIL (e.g. when candidate has 'No relocation' constraint).
        # The important thing is that the 2nd job's failure didn't crash the pipeline.
        assert result.ranked_count == 1


@pytest.mark.asyncio
async def test_execute_rank_re_rank(db_session, sample_candidate, sample_job):
    """execute_rank with re_rank=True re-evaluates already-ranked jobs."""
    with patch(
        "app.services.orchestrator.llm_orchestrator.LLMOrchestrator.execute"
    ) as mock:
        mock.return_value = mock_orchestrator_output(behavioral=50, career=50)
        await rank.execute_rank(db=db_session, user_id="test-user-id", re_rank=False)

    # Re-rank with different scores
    with patch(
        "app.services.orchestrator.llm_orchestrator.LLMOrchestrator.execute"
    ) as mock:
        mock.return_value = mock_orchestrator_output(behavioral=90, career=90)
        result = await rank.execute_rank(db=db_session, user_id="test-user-id", re_rank=True)

    assert result.ranked_count == 1
    assert result.shortlist[0].evaluation.behavioral_score == 90


@pytest.mark.asyncio
async def test_execute_rank_focus_area(db_session, sample_candidate):
    """execute_rank passes focus_area as guidance to the LLM prompt,
    not as a SQL pre-filter. Both jobs should still be ranked."""
    # Add two jobs with different titles
    job1 = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-ml",
        title="Machine Learning Engineer",
        company="MLCorp",
        location="Copenhagen",
        status="new",
    )
    job2 = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-web",
        title="Frontend Developer",
        company="WebCorp",
        location="Copenhagen",
        status="new",
    )
    db_session.add_all([job1, job2])
    await db_session.commit()

    with patch(
        "app.services.orchestrator.llm_orchestrator.LLMOrchestrator.execute"
    ) as mock:
        mock.return_value = mock_orchestrator_output()
        result = await rank.execute_rank(
            db=db_session,
            user_id="test-user-id",
            focus_area="machine learning",
            re_rank=False,
        )

    # focus_area is guidance passed to the LLM prompt, not a SQL filter.
    # Both jobs are ranked; the guidance tells the LLM to prefer the ML job.
    assert result.ranked_count == 2


@pytest.mark.asyncio
async def test_execute_rank_location_fail(db_session, sample_candidate):
    """execute_rank vetoes jobs with location FAIL."""
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-remote",
        title="ML Engineer",
        company="RemoteCorp",
        location="San Francisco, USA",
        status="new",
    )
    db_session.add(job)
    await db_session.commit()

    with patch(
        "app.services.orchestrator.llm_orchestrator.LLMOrchestrator.execute"
    ) as mock:
        mock.return_value = mock_orchestrator_output()
        result = await rank.execute_rank(db=db_session, user_id="test-user-id")

    # Location is now deterministic: San Francisco vs Copenhagen with "No relocation" → FAIL
    assert result.ranked_count == 1
    assert result.expired_or_vetoed == 1
    assert result.shortlist == []


@pytest.mark.asyncio
async def test_execute_rank_deadline_urgent(db_session, sample_candidate):
    """execute_rank marks deadline_urgent for jobs with deadline within 7 days."""
    from datetime import date, timedelta

    urgent_deadline = (date.today() + timedelta(days=3)).isoformat()
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-urgent",
        title="Urgent ML Role",
        company="FastCorp",
        location="Copenhagen",
        deadline=urgent_deadline,
        status="new",
    )
    db_session.add(job)
    await db_session.commit()

    with patch(
        "app.services.orchestrator.llm_orchestrator.LLMOrchestrator.execute"
    ) as mock:
        mock.return_value = mock_orchestrator_output()
        result = await rank.execute_rank(db=db_session, user_id="test-user-id")

    # Deadline urgency is now determined by rank_analyzer (3 days away = urgent)
    assert result.shortlist[0].evaluation.deadline_urgent is True


@pytest.mark.asyncio
async def test_get_rank_evaluation(db_session, sample_candidate, sample_job):
    """get_rank_evaluation returns the evaluation for a job."""
    # Create an evaluation
    eval_ = RankEvaluation(
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
        strengths=["Strong ML background"],
        gaps=["No K8s cert"],
        missing_keywords=["Kubernetes"],
        red_flags=[],
        language="en",
    )
    db_session.add(eval_)
    await db_session.commit()
    await db_session.refresh(eval_)

    fetched = await rank.get_rank_evaluation(db_session, sample_job.id, "test-user-id")
    assert fetched.id == eval_.id
    assert fetched.technical_score == 85


@pytest.mark.asyncio
async def test_get_rank_evaluation_not_found(db_session):
    """get_rank_evaluation raises NotFoundError for missing evaluation."""
    with pytest.raises(NotFoundError):
        await rank.get_rank_evaluation(db_session, "nonexistent-id", "test-user-id")


@pytest.mark.asyncio
async def test_list_ranked_jobs(db_session, sample_candidate):
    """list_ranked_jobs returns ranked jobs with filters."""
    # Create ranked jobs
    for i, score in enumerate([90, 74, 60, 40]):
        job = JobPosting(
            user_id="test-user-id",
            portal="linkedin",
            external_id=f"job-{i}",
            title=f"Job {i}",
            company="Corp",
            location="Copenhagen",
            status="ranked",
            rank_score=score,
            rank_verdict="Strong Fit" if score >= 75 else "Good Fit" if score >= 60 else "Moderate Fit",
        )
        db_session.add(job)
    await db_session.commit()

    # All jobs
    jobs = await rank.list_ranked_jobs(db_session, "test-user-id", limit=10)
    assert len(jobs) == 4

    # Filter by min_score
    jobs = await rank.list_ranked_jobs(db_session, "test-user-id", min_score=70, limit=10)
    assert len(jobs) == 2
    assert all(j.rank_score >= 70 for j in jobs)

    # Filter by verdict
    jobs = await rank.list_ranked_jobs(db_session, "test-user-id", verdict="Strong Fit", limit=10)
    assert len(jobs) == 1
    assert jobs[0].rank_verdict == "Strong Fit"


@pytest.mark.asyncio
async def test_compute_overall_score():
    """compute_overall_score applies correct weights."""
    # Technical 30%, Experience 25%, Behavioral 15%, Career 30%
    score = rank.compute_overall_score(100, 100, 100, 100)
    assert score == 100

    score = rank.compute_overall_score(80, 80, 80, 80)
    assert score == 80

    # Test with different scores
    score = rank.compute_overall_score(100, 0, 0, 0)
    assert score == 30  # 100 * 0.30

    score = rank.compute_overall_score(0, 100, 0, 0)
    assert score == 25  # 100 * 0.25

    score = rank.compute_overall_score(0, 0, 100, 0)
    assert score == 15  # 100 * 0.15

    score = rank.compute_overall_score(0, 0, 0, 100)
    assert score == 30  # 100 * 0.30


@pytest.mark.asyncio
async def test_score_to_verdict():
    """score_to_verdict maps scores to correct bands."""
    assert rank.score_to_verdict(90) == "Strong Fit"
    assert rank.score_to_verdict(75) == "Strong Fit"
    assert rank.score_to_verdict(70) == "Good Fit"
    assert rank.score_to_verdict(60) == "Good Fit"
    assert rank.score_to_verdict(50) == "Moderate Fit"
    assert rank.score_to_verdict(45) == "Moderate Fit"
    assert rank.score_to_verdict(35) == "Weak Fit"
    assert rank.score_to_verdict(30) == "Weak Fit"
    assert rank.score_to_verdict(20) == "Poor Fit"
    assert rank.score_to_verdict(0) == "Poor Fit"