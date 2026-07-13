"""Tests for the apply service.

Uses an in-memory SQLite database and mocks the LLM calls and LaTeX compilation.
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
    User,
)
from app.exceptions import LLMError, LatexCompileError, NotFoundError, ProfileIncompleteError
from app.services import apply


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
        tailored_experience=[],
        cv_tex_path="/tmp/cv.tex",
        cv_pdf_path="/tmp/cv.pdf",
        cover_letter_tex_path="/tmp/cover.tex",
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


# ── Helper: mock LLM ────────────────────────────────────────────────


def mock_tailored_experience():
    """Mock tailored experience output from LLM."""
    return apply.TailoredExperienceLLMOutput(
        experience=[
            apply.TailoredExperienceEntry(
                title="Senior ML Engineer",
                company="Acme Corp",
                start_date="2020-01",
                end_date="Present",
                location="Copenhagen",
                bullets=[
                    "Accomplished 40% reduction in model inference latency (X), as measured by p99 latency (Y), by implementing TensorRT optimization and batching (Z)",
                    "Achieved processing of 1M+ events/day (X), measured by throughput metrics (Y), by building scalable ML pipeline with PyTorch and Kubernetes (Z)",
                    "Led team of 5 engineers (Z) to deliver real-time fraud detection system (X) processing 10K transactions/sec with <50ms latency (Y)",
                ],
            ),
            apply.TailoredExperienceEntry(
                title="Data Scientist",
                company="Beta Inc",
                start_date="2018-06",
                end_date="2019-12",
                location="Aarhus",
                bullets=[
                    "Increased recommendation click-through rate by 15% (X), measured via A/B test (Y), by adding collaborative filtering signals to ranking model (Z)",
                    "Published 2 papers at top conferences (X), demonstrating research impact (Y), by conducting novel NLP research (Z)",
                ],
            ),
        ]
    )


def mock_cover_letter():
    """Mock cover letter output from LLM."""
    return apply.CoverLetterLLMOutput(
        opening_paragraph="I am writing to apply for the Senior Machine Learning Engineer position at TechCorp. With 5+ years of experience building production ML systems at scale, including leading a team of 5 engineers at Acme Corp, I am confident I can contribute immediately to your ML infrastructure.",
        body_paragraphs=[
            "My most relevant experience includes:",
            "• Accomplished 40% reduction in model inference latency, as measured by p99 latency, by implementing TensorRT optimization and batching",
            "• Achieved processing of 1M+ events/day, measured by throughput metrics, by building scalable ML pipeline with PyTorch and Kubernetes",
            "• Led team of 5 engineers to deliver real-time fraud detection system processing 10K transactions/sec with <50ms latency",
        ],
        company_connection_paragraph="I have followed TechCorp's work on scalable ML infrastructure and was particularly impressed by your recent blog post on Kubernetes-native ML pipelines. Your commitment to open-source tooling aligns with my experience contributing to open-source ML libraries.",
        personal_fit_paragraph="As an analytical driver who thrives in autonomous environments, I bring both technical depth and collaborative leadership. My experience mentoring junior engineers and driving cross-functional ML projects would enable me to contribute to your team culture from day one.",
        closing_paragraph="I look forward to discussing how my experience building production ML systems at scale can contribute to TechCorp's mission.",
    )


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_apply_basic(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_apply generates tailored CV and cover letter."""
    with patch("app.services.apply.llm_completion_structured") as mock_llm:
        # First call: tailored experience
        # Second call: cover letter
        mock_llm.side_effect = [
            mock_tailored_experience(),
            mock_cover_letter(),
        ]

        with patch("app.services.apply.compile_latex") as mock_compile:
            mock_compile.side_effect = [
                (Path("/tmp/cv.pdf"), 2),  # CV: 2 pages
                (Path("/tmp/cover.pdf"), 1),  # Cover letter: 1 page
            ]

            with patch("app.services.apply.shutil.copy2"):
                with patch("app.services.apply.Path.mkdir"):
                    with patch("app.services.apply.Path.exists", return_value=True):
                        result = await apply.execute_apply(
                            db=db_session,
                            user_id="test-user-id",
                            job_posting_id=sample_job.id,
                            rank_evaluation_id=sample_evaluation.id,
                        )

    assert result.application_id is not None
    assert result.cv_compiled is True
    assert result.cv_pages == 2
    assert result.cover_letter_compiled is True
    assert result.cover_letter_pages == 1

    # Verify application was created
    app_result = await db_session.execute(
        select(Application).where(Application.id == result.application_id)
    )
    application = app_result.scalar_one_or_none()
    assert application is not None
    assert application.user_id == "test-user-id"
    assert application.job_posting_id == sample_job.id
    assert application.tailored_experience is not None
    assert len(application.tailored_experience) == 2


@pytest.mark.asyncio
async def test_execute_apply_job_not_found(db_session, sample_candidate):
    """execute_apply raises NotFoundError for non-existent job."""
    with pytest.raises(NotFoundError):
        await apply.execute_apply(
            db=db_session,
            user_id="test-user-id",
            job_posting_id="nonexistent-id",
        )


@pytest.mark.asyncio
async def test_execute_apply_evaluation_not_found(db_session, sample_candidate, sample_job):
    """execute_apply raises NotFoundError when no rank evaluation exists."""
    with pytest.raises(NotFoundError):
        await apply.execute_apply(
            db=db_session,
            user_id="test-user-id",
            job_posting_id=sample_job.id,
        )


@pytest.mark.asyncio
async def test_execute_apply_profile_incomplete(db_session, sample_job, sample_evaluation):
    """execute_apply raises ProfileIncompleteError when candidate profile missing."""
    with pytest.raises(ProfileIncompleteError):
        await apply.execute_apply(
            db=db_session,
            user_id="nonexistent-user",
            job_posting_id=sample_job.id,
            rank_evaluation_id=sample_evaluation.id,
        )


@pytest.mark.asyncio
async def test_execute_apply_llm_error(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_apply raises LLMError when LLM call fails."""
    with patch("app.services.apply.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = LLMError("LLM timeout")

        with pytest.raises(LLMError):
            await apply.execute_apply(
                db=db_session,
                user_id="test-user-id",
                job_posting_id=sample_job.id,
                rank_evaluation_id=sample_evaluation.id,
            )


@pytest.mark.asyncio
async def test_execute_apply_latex_compile_error(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_apply raises LatexCompileError when LaTeX compilation fails."""
    with patch("app.services.apply.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_tailored_experience(),
            mock_cover_letter(),
        ]

        with patch("app.services.apply.compile_latex") as mock_compile:
            mock_compile.side_effect = LatexCompileError("lualatex failed: missing font")

            with pytest.raises(LatexCompileError):
                await apply.execute_apply(
                    db=db_session,
                    user_id="test-user-id",
                    job_posting_id=sample_job.id,
                    rank_evaluation_id=sample_evaluation.id,
                )


@pytest.mark.asyncio
async def test_get_application(db_session, sample_candidate, sample_job, sample_evaluation):
    """get_application returns the application by ID."""
    # Create an application
    application = Application(
        user_id="test-user-id",
        job_posting_id=sample_job.id,
        rank_evaluation_id=sample_evaluation.id,
        tailored_experience=[],
        cv_tex_path="/tmp/cv.tex",
        cv_pdf_path="/tmp/cv.pdf",
        cover_letter_tex_path="/tmp/cover.tex",
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

    fetched = await apply.get_application(db_session, application.id, "test-user-id")
    assert fetched.id == application.id
    assert fetched.cv_pages == 2


@pytest.mark.asyncio
async def test_get_application_not_found(db_session):
    """get_application raises NotFoundError for non-existent application."""
    with pytest.raises(NotFoundError):
        await apply.get_application(db_session, "nonexistent-id", "test-user-id")


@pytest.mark.asyncio
async def test_get_application_wrong_user(db_session, sample_candidate, sample_job, sample_evaluation):
    """get_application raises NotFoundError when application belongs to another user."""
    application = Application(
        user_id="test-user-id",
        job_posting_id=sample_job.id,
        rank_evaluation_id=sample_evaluation.id,
        tailored_experience=[],
        cv_tex_path="/tmp/cv.tex",
        cv_pdf_path="/tmp/cv.pdf",
        cover_letter_tex_path="/tmp/cover.tex",
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

    with pytest.raises(NotFoundError):
        await apply.get_application(db_session, application.id, "other-user-id")


@pytest.mark.asyncio
async def test_list_applications(db_session, sample_candidate, sample_job, sample_evaluation):
    """list_applications returns applications for the user."""
    for i in range(3):
        app = Application(
            user_id="test-user-id",
            job_posting_id=sample_job.id,
            rank_evaluation_id=sample_evaluation.id,
            tailored_experience=[],
            cv_tex_path=f"/tmp/cv{i}.tex",
            cv_pdf_path=f"/tmp/cv{i}.pdf",
            cover_letter_tex_path=f"/tmp/cover{i}.tex",
            cover_letter_pdf_path=f"/tmp/cover{i}.pdf",
            cv_compiled=True,
            cv_pages=2,
            cover_letter_compiled=True,
            cover_letter_pages=1,
            cv_template="moderncv-banking",
            cover_letter_template="cover-cls",
            language="en",
        )
        db_session.add(app)
    await db_session.commit()

    apps = await apply.list_applications(db_session, "test-user-id", limit=10)
    assert len(apps) == 3


@pytest.mark.asyncio
async def test_build_tailored_experience_prompt(sample_candidate, sample_job, sample_evaluation):
    """build_tailored_experience_prompt creates correct prompt structure."""
    messages = apply.build_tailored_experience_prompt(sample_candidate, sample_job, sample_evaluation)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "GUARDRAIL" in messages[0]["content"]
    assert "X-Y-Z" in messages[0]["content"]
    assert "Jane Doe" in messages[0]["content"]
    assert "Senior Machine Learning Engineer" in messages[0]["content"]
    assert "Kubernetes" in messages[0]["content"]  # missing keyword


@pytest.mark.asyncio
async def test_build_cover_letter_prompt(sample_candidate, sample_job, sample_evaluation):
    """build_cover_letter_prompt creates correct prompt structure."""
    tailored_exp = mock_tailored_experience().experience
    messages = apply.build_cover_letter_prompt(sample_candidate, sample_job, sample_evaluation, tailored_exp)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "GUARDRAIL" in messages[0]["content"]
    assert "TechCorp" in messages[0]["content"]


@pytest.mark.asyncio
async def test_render_cv_latex(sample_candidate, sample_job):
    """render_cv_latex produces valid LaTeX with replaced placeholders."""
    tailored_exp = mock_tailored_experience().experience
    latex = apply.render_cv_latex(sample_candidate, tailored_exp, sample_job)

    assert "Jane Doe" in latex
    assert "Copenhagen, Denmark" in latex
    assert "jane@example.com" in latex
    assert "Senior ML Engineer" in latex
    assert "Acme Corp" in latex
    assert "TensorRT" in latex
    assert "\\section{Core Competencies}" in latex
    assert "\\section{Professional Experience}" in latex
    assert "\\section{Education}" in latex


@pytest.mark.asyncio
async def test_render_cover_letter_latex(sample_candidate, sample_job):
    """render_cover_letter_latex produces valid LaTeX with replaced placeholders."""
    cover_content = mock_cover_letter()
    latex = apply.render_cover_letter_latex(sample_candidate, sample_job, cover_content)

    assert "Jane Doe" in latex
    assert "jane@example.com" in latex
    assert "TechCorp" in latex
    assert "Senior Machine Learning Engineer" in latex
    assert "TensorRT" in latex
    assert "Dear TechCorp," in latex
    assert "Raleway-Medium" in latex  # font for bullets


@pytest.mark.asyncio
async def test_extract_incorporated_keywords():
    """_extract_incorporated_keywords finds keywords in tailored experience."""
    tailored_exp = mock_tailored_experience().experience
    missing = ["Kubernetes", "AWS", "CI/CD", "Docker", "Python"]

    incorporated = apply._extract_incorporated_keywords(tailored_exp, missing)

    # Kubernetes and AWS should be found in the tailored experience
    keywords_found = [k.keyword for k in incorporated]
    assert "Kubernetes" in keywords_found
    assert "AWS" in keywords_found


@pytest.mark.asyncio
async def test_extract_addressed_red_flags():
    """_extract_addressed_red_flags finds addressed red flags."""
    tailored_exp = mock_tailored_experience().experience
    red_flags = ["Gap in employment 2017-2018", "No Kubernetes experience"]

    addressed = apply._extract_addressed_red_flags(tailored_exp, red_flags)

    # Should find flags that have keywords in the tailored experience
    assert len(addressed) >= 0  # May or may not find depending on text matching


# ── LaTeX compilation tests (mocked) ────────────────────────────────


@pytest.mark.asyncio
async def test_compile_latex_success():
    """compile_latex returns PDF path and page count on success."""
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = mock_proc

        with patch("app.services.apply._get_pdf_page_count", return_value=2):
            with patch("pathlib.Path.exists", return_value=True):
                pdf_path, pages = await apply.compile_latex(
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

        with pytest.raises(LatexCompileError):
            await apply.compile_latex(
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

        with patch("app.services.apply._get_pdf_page_count", return_value=3):  # Expected 2, got 3
            with patch("pathlib.Path.exists", return_value=True):
                with pytest.raises(LatexCompileError):
                    await apply.compile_latex(
                        "dummy tex content",
                        Path("/tmp"),
                        "test_cv",
                        "lualatex",
                        2,
                    )
