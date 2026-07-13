"""Tests for the upskill service.

Uses an in-memory SQLite database and mocks the LLM calls and web searches.
"""

import asyncio
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
    RankEvaluation,
    Upskill,
    User,
)
from app.db.models import Base
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.services import upskill


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
def sample_candidate(db_session):
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
    db_session.commit()
    db_session.refresh(candidate)
    return candidate


@pytest.fixture
def sample_job(db_session, sample_candidate):
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
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.fixture
def sample_evaluation(db_session, sample_candidate, sample_job):
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
    db_session.commit()
    db_session.refresh(evaluation)
    return evaluation


# ── Helper: mock LLM ────────────────────────────────────────────────


def mock_pass1_output():
    """Mock Pass 1 (hard skill diff) output."""
    from app.services.upskill import HardSkillGapsLLMOutput, HardSkillGap

    return upskill.HardSkillGapsLLMOutput(
        gaps=[
            upskill.HardSkillGap(
                skill="Kubernetes",
                type="hard",
                frequency=3,
                total_jobs=3,
                fit_weighted_score=2.5,
                jobs_mentioning=["TechCorp", "DataCorp", "AIStartup"],
                evidence="Required in 3/3 job postings",
            ),
            upskill.HardSkillGap(
                skill="AWS",
                type="hard",
                frequency=2,
                total_jobs=3,
                fit_weighted_score=1.8,
                jobs_mentioning=["TechCorp", "DataCorp"],
                evidence="Required in 2/3 job postings",
            ),
            upskill.HardSkillGap(
                skill="CI/CD",
                type="hard",
                frequency=2,
                total_jobs=3,
                fit_weighted_score=1.5,
                jobs_mentioning=["TechCorp", "AIStartup"],
                evidence="Required in 2/3 job postings",
            ),
        ]
    )


def mock_pass2_output():
    """Mock Pass 2 (LLM synthesis) output."""
    from app.services.upskill import SynthesizedGapsLLMOutput, SynthesizedGap

    return upskill.SynthesizedGapsLLMOutput(
        gaps=[
            upskill.SynthesizedGap(
                skill="MLOps",
                type="tooling",
                priority="Critical",
                evidence="Kubernetes + CI/CD gaps indicate missing MLOps pipeline knowledge",
                source="Pass 1 + LLM inference",
            ),
            upskill.SynthesizedGap(
                skill="Cloud Architecture (AWS)",
                type="domain",
                priority="High",
                evidence="AWS required in 2/3 jobs; candidate has Docker/K8s but no cloud architecture",
                source="Pass 1 + LLM inference",
            ),
            upskill.SynthesizedGap(
                skill="MLOps Certification",
                type="credential",
                priority="Medium",
                evidence="No formal MLOps certification; would strengthen profile",
                source="LLM inference",
            ),
        ]
    )


def mock_heatmap_output():
    """Mock heatmap output."""
    from app.services.upskill import GapHeatmapLLMOutput, HeatmapEntry

    return upskill.GapHeatmapLLMOutput(
        heatmap=[
            upskill.HeatmapEntry(
                skill="Kubernetes",
                type="hard",
                priority="Critical",
                gap_source="Pass 1: 3/3 jobs, score 2.5",
                evidence="Required in all 3 target jobs",
            ),
            upskill.HeatmapEntry(
                skill="AWS",
                type="hard",
                priority="High",
                gap_source="Pass 1: 2/3 jobs, score 1.8",
                evidence="Required in 2/3 target jobs",
            ),
            upskill.HeatmapEntry(
                skill="CI/CD",
                type="hard",
                priority="High",
                gap_source="Pass 1: 2/3 jobs, score 1.5",
                evidence="Required in 2/3 target jobs",
            ),
            upskill.HeatmapEntry(
                skill="MLOps",
                type="tooling",
                priority="Critical",
                gap_source="Pass 2: LLM synthesis",
                evidence="Kubernetes + CI/CD gaps indicate missing MLOps pipeline knowledge",
            ),
            upskill.HeatmapEntry(
                skill="Cloud Architecture (AWS)",
                type="domain",
                priority="High",
                gap_source="Pass 2: LLM synthesis",
                evidence="AWS required in 2/3 jobs; candidate has Docker/K8s but no cloud architecture",
            ),
            upskill.HeatmapEntry(
                skill="MLOps Certification",
                type="credential",
                priority="Medium",
                gap_source="Pass 2: LLM synthesis",
                evidence="No formal MLOps certification; would strengthen profile",
            ),
        ]
    )


def mock_learning_plan_output():
    """Mock learning plan output."""
    from app.services.upskill import LearningPlanLLMOutput, LearningPlanItem

    return upskill.LearningPlanLLMOutput(
        plan=[
            upskill.LearningPlanItem(
                skill="Kubernetes",
                priority="Critical",
                resources=[
                    {
                        "title": "Kubernetes for Developers (CNCF)",
                        "url": "https://training.linuxfoundation.org/training/kubernetes-for-developers/",
                        "format": "course",
                        "estimated_hours": 20,
                        "cost": "paid",
                        "quality_score": 9,
                    },
                    {
                        "title": "Kubernetes the Hard Way (Kelsey Hightower)",
                        "url": "https://github.com/kelseyhightower/kubernetes-the-hard-way",
                        "format": "article",
                        "estimated_hours": 10,
                        "cost": "free",
                        "quality_score": 10,
                    },
                ],
                estimated_weeks=3,
                prerequisites=["Docker", "Linux basics"],
            ),
            upskill.LearningPlanItem(
                skill="AWS Cloud Architecture",
                priority="High",
                resources=[
                    {
                        "title": "AWS Certified Solutions Architect - Associate",
                        "url": "https://aws.amazon.com/certification/certified-solutions-architect-associate/",
                        "format": "certification",
                        "estimated_hours": 40,
                        "cost": "paid",
                        "quality_score": 9,
                    },
                    {
                        "title": "AWS Well-Architected Framework",
                        "url": "https://aws.amazon.com/architecture/well-architected/",
                        "format": "article",
                        "estimated_hours": 5,
                        "cost": "free",
                        "quality_score": 9,
                    },
                ],
                estimated_weeks=4,
                prerequisites=["Cloud basics", "Linux"],
            ),
            upskill.LearningPlanItem(
                skill="MLOps",
                priority="Critical",
                resources=[
                    {
                        "title": "MLOps Fundamentals (Google Cloud)",
                        "url": "https://www.coursera.org/learn/mlops-fundamentals",
                        "format": "course",
                        "estimated_hours": 15,
                        "cost": "free",
                        "quality_score": 8,
                    },
                    {
                        "title": "MLOps with MLflow",
                        "url": "https://mlflow.org/docs/latest/tutorials-and-examples/index.html",
                        "format": "article",
                        "estimated_hours": 8,
                        "cost": "free",
                        "quality_score": 8,
                    },
                ],
                estimated_weeks=3,
                prerequisites=["Kubernetes", "Docker", "ML basics"],
            ),
        ]
    )


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_upskill_basic(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_upskill runs a full analysis and returns results."""
    with patch("app.services.upskill.llm_completion_structured") as mock_llm:
        # Mock all LLM calls in sequence
        mock_llm.side_effect = [
            mock_pass1_output(),      # Pass 1: hard skill diff
            mock_pass2_output(),      # Pass 2: LLM synthesis
            mock_heatmap_output(),    # Heatmap
            mock_learning_plan_output(),  # Learning plan
        ]

        with patch("app.services.upskill._scan_cv_folder", return_value=[]):
            with patch("app.services.upskill._scan_linkedin_folder", return_value=[]):
                with patch("app.services.upskill._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.upskill._scan_references_folder", return_value=[]):
                        with patch("app.services.upskill._scan_github_profile", return_value=[]):
                            with patch("app.services.upskill._scan_other_urls", return_value=[]):
                                upskill_result = await upskill.execute_upskill(
                                    db=db_session,
                                    user_id="test-user-id",
                                    mode="aggregate",
                                )

    assert upskill_result.id is not None
    assert upskill_result.user_id == "test-user-id"
    assert upskill_result.candidate_id == sample_candidate.id
    assert upskill_result.status == "completed"
    assert upskill_result.mode == "aggregate"

    # Check hard skill gaps
    assert upskill_result.hard_skill_gaps is not None
    assert len(upskill_result.hard_skill_gaps) == 3
    skills = [g["skill"] for g in upskill_result.hard_skill_gaps]
    assert "Kubernetes" in skills
    assert "AWS" in skills
    assert "CI/CD" in skills

    # Check synthesized gaps
    assert upskill_result.synthesized_gaps is not None
    assert len(upskill_result.synthesized_gaps) == 3

    # Check heatmap
    assert upskill_result.gap_heatmap is not None
    assert len(upskill_result.gap_heatmap) == 6

    # Check learning plan
    assert upskill_result.learning_plan is not None
    assert len(upskill_result.learning_plan) == 3


@pytest.mark.asyncio
async def test_execute_upskill_targeted_mode(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_upskill in targeted mode analyses a single job."""
    with patch("app.services.upskill.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_pass1_output(),
            mock_pass2_output(),
            mock_heatmap_output(),
            mock_learning_plan_output(),
        ]

        with patch("app.services.upskill._scan_cv_folder", return_value=[]):
            with patch("app.services.upskill._scan_linkedin_folder", return_value=[]):
                with patch("app.services.upskill._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.upskill._scan_references_folder", return_value=[]):
                        with patch("app.services.upskill._scan_github_profile", return_value=[]):
                            with patch("app.services.upskill._scan_other_urls", return_value=[]):
                                upskill_result = await upskill.execute_upskill(
                                    db=db_session,
                                    user_id="test-user-id",
                                    mode="targeted",
                                    target_job_posting_id=sample_job.id,
                                )

    assert upskill_result.mode == "targeted"
    assert upskill_result.target_job_posting_id == sample_job.id


@pytest.mark.asyncio
async def test_execute_upskill_profile_incomplete(db_session):
    """execute_upskill raises ProfileIncompleteError when candidate profile missing."""
    with pytest.raises(ProfileIncompleteError):
        await upskill.execute_upskill(
            db=db_session,
            user_id="nonexistent-user",
            mode="aggregate",
        )


@pytest.mark.asyncio
async def test_execute_upskill_job_not_found(db_session, sample_candidate):
    """execute_upskill raises NotFoundError for non-existent target job."""
    with pytest.raises(NotFoundError):
        await upskill.execute_upskill(
            db=db_session,
            user_id="test-user-id",
            mode="targeted",
            target_job_posting_id="nonexistent-id",
        )


@pytest.mark.asyncio
async def test_execute_upskill_llm_error(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_upskill raises LLMError when LLM call fails."""
    with patch("app.services.upskill.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = Exception("LLM timeout")

        with patch("app.services.upskill._scan_cv_folder", return_value=[]):
            with patch("app.services.upskill._scan_linkedin_folder", return_value=[]):
                with patch("app.services.upskill._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.upskill._scan_references_folder", return_value=[]):
                        with patch("app.services.upskill._scan_github_profile", return_value=[]):
                            with patch("app.services.upskill._scan_other_urls", return_value=[]):
                                with pytest.raises(Exception):  # LLMError or similar
                                    await upskill.execute_upskill(
                                        db=db_session,
                                        user_id="test-user-id",
                                        mode="aggregate",
                                    )


@pytest.mark.asyncio
async def test_get_upskill(db_session, sample_candidate, sample_job, sample_evaluation):
    """get_upskill returns the upskill by ID."""
    with patch("app.services.upskill.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_pass1_output(),
            mock_pass2_output(),
            mock_heatmap_output(),
            mock_learning_plan_output(),
        ]

        with patch("app.services.upskill._scan_cv_folder", return_value=[]):
            with patch("app.services.upskill._scan_linkedin_folder", return_value=[]):
                with patch("app.services.upskill._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.upskill._scan_references_folder", return_value=[]):
                        with patch("app.services.upskill._scan_github_profile", return_value=[]):
                            with patch("app.services.upskill._scan_other_urls", return_value=[]):
                                created = await upskill.execute_upskill(
                                    db=db_session,
                                    user_id="test-user-id",
                                    mode="aggregate",
                                )

    fetched = await upskill.get_upskill(db_session, created.id, "test-user-id")
    assert fetched.id == created.id
    assert fetched.status == "completed"


@pytest.mark.asyncio
async def test_get_upskill_not_found(db_session):
    """get_upskill raises NotFoundError for non-existent upskill."""
    with pytest.raises(NotFoundError):
        await upskill.get_upskill(db_session, "nonexistent-id", "test-user-id")


@pytest.mark.asyncio
async def test_get_upskill_wrong_user(db_session, sample_candidate, sample_job, sample_evaluation):
    """get_upskill raises NotFoundError when upskill belongs to another user."""
    with patch("app.services.upskill.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_pass1_output(),
            mock_pass2_output(),
            mock_heatmap_output(),
            mock_learning_plan_output(),
        ]

        with patch("app.services.upskill._scan_cv_folder", return_value=[]):
            with patch("app.services.upskill._scan_linkedin_folder", return_value=[]):
                with patch("app.services.upskill._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.upskill._scan_references_folder", return_value=[]):
                        with patch("app.services.upskill._scan_github_profile", return_value=[]):
                            with patch("app.services.upskill._scan_other_urls", return_value=[]):
                                created = await upskill.execute_upskill(
                                    db=db_session,
                                    user_id="test-user-id",
                                    mode="aggregate",
                                )

    with pytest.raises(NotFoundError):
        await upskill.get_upskill(db_session, created.id, "other-user-id")


@pytest.mark.asyncio
async def test_list_upskills(db_session, sample_candidate, sample_job, sample_evaluation):
    """list_upskills returns upskills for the user."""
    with patch("app.services.upskill.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_pass1_output(),
            mock_pass2_output(),
            mock_heatmap_output(),
            mock_learning_plan_output(),
        ]

        with patch("app.services.upskill._scan_cv_folder", return_value=[]):
            with patch("app.services.upskill._scan_linkedin_folder", return_value=[]):
                with patch("app.services.upskill._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.upskill._scan_references_folder", return_value=[]):
                        with patch("app.services.upskill._scan_github_profile", return_value=[]):
                            with patch("app.services.upskill._scan_other_urls", return_value=[]):
                                # Create 3 upskills
                                for i in range(3):
                                    await upskill.execute_upskill(
                                        db=db_session,
                                        user_id="test-user-id",
                                        mode="aggregate",
                                    )

    upskills = await upskill.list_upskills(db_session, "test-user-id", limit=10)
    assert len(upskills) == 3


# ── Schema validation tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_upskill_request_schema():
    """UpskillRequest schema validates required fields."""
    from app.schemas.upskill import UpskillRequest

    payload = upskill.UpskillRequest(
        mode="aggregate",
        target_job_url=None,
        target_job_posting_id=None,
    )
    assert payload.mode == "aggregate"
    assert payload.target_job_url is None
    assert payload.target_job_posting_id is None


@pytest.mark.asyncio
async def test_upskill_request_schema_targeted():
    """UpskillRequest schema validates targeted mode."""
    from app.schemas.upskill import UpskillRequest

    payload = upskill.UpskillRequest(
        mode="targeted",
        target_job_url="https://linkedin.com/jobs/123",
        target_job_posting_id="job-123",
    )
    assert payload.mode == "targeted"
    assert payload.target_job_url == "https://linkedin.com/jobs/123"
    assert payload.target_job_posting_id == "job-123"


# ── LaTeX compilation tests (mocked) ────────────────────────────────


@pytest.mark.asyncio
async def test_compile_latex_success():
    """compile_latex returns PDF path and page count on success."""
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = mock_proc

        with patch("app.services.upskill._get_pdf_page_count", return_value=2):
            with patch("pathlib.Path.exists", return_value=True):
                pdf_path, pages = await upskill.compile_latex(
                    "dummy tex content",
                    Path("/tmp"),
                    "test_cv",
                    "lualatex",
                    2,
                )

    assert pages == 2
    assert pdf_path.name == "test_cv.pdf"


@pytest.mark.asyncio
async def test_compile_latex_failure():
    """compile_latex raises LatexCompileError on compilation failure."""
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: missing font"))
        mock_exec.return_value = mock_proc

        with pytest.raises(upskill.LatexCompileError):
            await upskill.compile_latex(
                "dummy tex content",
                Path("/tmp"),
                "test_cv",
                "lualatex",
                2,
            )


@pytest.mark.asyncio
async def test_compile_latex_wrong_page_count():
    """compile_latex raises LatexCompileError on wrong page count."""
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = mock_proc

        with patch("app.services.upskill._get_pdf_page_count", return_value=3):  # Expected 2, got 3
            with patch("pathlib.Path.exists", return_value=True):
                with pytest.raises(upskill.LatexCompileError):
                    await upskill.compile_latex(
                        "dummy tex content",
                        Path("/tmp"),
                        "test_cv",
                        "lualatex",
                        2,
                    )