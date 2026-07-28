"""Flow-mock tests for the JSON/Typst pipeline (Fase 1.3).

Verifies structural correctness — prompt builder output, fresh-context
for reviewer, sanitizer integration, revise round-trip, and that an
enlatado GenerateCVOutput renders through the Typst compile path.

These are "fontanería" tests: they mock the LLM and test the plumbing.
Quality comparison (LaTeX vs Typst with real LLM output) is a separate
gate that requires API credentials and is NOT tested here.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, CandidateProfile, JobPosting, RankEvaluation, User
from app.schemas.apply import ReviewFeedback
from app.schemas.cv import CV, CVMetadata, CoverLetter, GenerateCVOutput


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


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
            profile_statement="Experienced ML engineer with 8 years in NLP.",
            location="Copenhagen, Denmark",
            skills={
                "programming_ml": [
                    {"language": "Python", "proficiency": "expert"},
                    {"language": "SQL", "proficiency": "advanced"},
                ],
                "domain_expertise": ["Machine Learning", "NLP"],
                "software_tools": ["Git", "Docker"],
            },
            experience=[
                {
                    "title": "Senior ML Engineer",
                    "company": "AI Corp",
                    "start_date": "2020-01",
                    "end_date": "Present",
                    "bullets": [
                        "Built NLP pipelines reducing latency by 40%",
                        "Led team of 3 engineers on LLM fine-tuning",
                    ],
                },
                {
                    "title": "Data Scientist",
                    "company": "DataCo",
                    "start_date": "2017-03",
                    "end_date": "2019-12",
                    "bullets": [
                        "Developed recommendation system serving 1M users",
                        "Automated ETL pipelines with Airflow",
                    ],
                },
            ],
            education=[
                {"degree": "M.Sc. in Computer Science", "institution": "DTU", "period": "2015-2017"},
            ],
            languages=[
                {"language": "English", "proficiency": "native"},
                {"language": "Danish", "proficiency": "fluent"},
            ],
        )
        session.add(candidate)
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
async def candidate(db_session) -> CandidateProfile:
    from sqlalchemy import select
    stmt = select(CandidateProfile).where(CandidateProfile.user_id == "test-user-id")
    c = (await db_session.execute(stmt)).scalar_one_or_none()
    return c


@pytest.fixture
def job() -> JobPosting:
    return JobPosting(
        id="test-job-flow",
        user_id="test-user-id",
        portal="test",
        external_id="test-job-flow",
        title="Senior ML Engineer",
        company="TechCorp",
        description="We are looking for a Senior ML Engineer with NLP experience.",
        requirements=[
            "Python", "PyTorch", "NLP", "Transformers", "Kubernetes",
            "AWS", "Docker", "CI/CD",
        ],
        language="en",
    )


@pytest.fixture
def evaluation() -> RankEvaluation:
    return RankEvaluation(
        id="test-eval-flow",
        user_id="test-user-id",
        job_posting_id="test-job-flow",
        overall_score=72,
        missing_keywords=["Kubernetes", "AWS", "CI/CD"],
        red_flags=["Gap in employment 2017-2018"],
        behavioral_score=75,
        career_score=80,
        location_status="PASS",
        strengths=["Strong NLP background", "Leadership experience"],
        gaps=["No cloud deployment experience"],
    )


# ── Enlatado CV data for Typst compile test ──────────────────────────


ENLATADO_CV_DICT = {
    "first_name": "Test",
    "last_name": "User",
    "email": "test@example.com",
    "phone": "+45 1234 5678",
    "location": "Copenhagen, Denmark",
    "linkedin": "https://linkedin.com/in/testuser",
    "language": "en",
    "profile_statement": "Senior ML Engineer with 8 years of NLP experience.",
    "core_competencies": ["NLP", "Machine Learning", "LLMs", "Python", "Leadership"],
    "skills": [
        {
            "label": "Programming Languages",
            "skills": [
                {"name": "Python", "proficiency": "expert"},
                {"name": "SQL", "proficiency": "advanced"},
            ],
        },
        {
            "label": "ML Frameworks",
            "skills": [
                {"name": "PyTorch", "proficiency": "advanced"},
                {"name": "Transformers", "proficiency": "advanced"},
            ],
        },
        {
            "label": "Languages",
            "skills": [
                {"name": "English", "proficiency": "advanced"},
                {"name": "Danish", "proficiency": "intermediate"},
            ],
        },
    ],
    "experience": [
        {
            "title": "Senior ML Engineer",
            "company": "AI Corp",
            "location": "Copenhagen",
            "date_range": {"start": "2020-01", "end": "Present"},
            "bullets": [
                "Built NLP pipelines with Transformers, reducing inference latency by 40%",
                "Led a team of 3 engineers on LLM fine-tuning for production",
            ],
        },
        {
            "title": "Data Scientist",
            "company": "DataCo",
            "location": "Copenhagen",
            "date_range": {"start": "2017-03", "end": "2019-12"},
            "bullets": [
                "Developed a PyTorch-based recommendation system serving 1M users",
                "Automated ETL pipelines with Airflow and Docker",
            ],
        },
    ],
    "education": [
        {
            "degree": "M.Sc. in Computer Science",
            "institution": "DTU",
            "date_range": {"start": "2015", "end": "2017"},
            "period": "2015-2017",
        },
    ],
    "certifications": [],
    "projects": [],
    "awards": [],
    "publications": [],
    "references": [],
}


ENLATADO_OUTPUT_DICT = {
    "cv": ENLATADO_CV_DICT,
    "metadata": {
        "language": "en",
        "incorporated_keywords": [
            {"keyword": "NLP", "where_incorporated": "profile statement, experience bullet 1"},
            {"keyword": "PyTorch", "where_incorporated": "experience bullet 2"},
        ],
        "addressed_red_flags": [
            {"red_flag": "Gap in employment 2017-2018", "how_addressed": "Framed as career change period"},
        ],
    },
}


ENLATADO_REVIEW_DICT = {
    "overall_assessment": "CV is solid with good X-Y-Z formula but missing a key keyword.",
    "passes": ["Strong profile statement", "Good use of metrics"],
    "issues": [
        {
            "type": "missing_keyword",
            "severity": "medium",
            "description": "Kubernetes not mentioned in any bullet",
            "location": "experience",
            "suggestion": "Add Kubernetes to bullet 2",
        },
    ],
    "strong_recommendations": ["Incorporate Kubernetes"],
}


# ═══════════════════════════════════════════════════════════════════════
# 1. Prompt builder structure tests
# ═══════════════════════════════════════════════════════════════════════


class TestPromptBuilders:
    """Verify every prompt builder produces valid system+user messages."""

    def test_drafter_prompt_has_schema(self, candidate, job, evaluation):
        from app.services.apply_json import build_json_drafter_prompt

        messages = build_json_drafter_prompt(candidate, job, evaluation)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        # System msg contains the GenerateCVOutput schema
        assert "GenerateCVOutput" in messages[0]["content"]
        # User msg contains candidate + job info
        assert candidate.profile_statement in messages[1]["content"]
        assert job.title in messages[1]["content"]
        # Evaluation insights included
        assert "Kubernetes" in messages[1]["content"]  # missing keyword

    def test_drafter_prompt_without_evaluation(self, candidate, job):
        from app.services.apply_json import build_json_drafter_prompt

        messages = build_json_drafter_prompt(candidate, job)
        assert len(messages) == 2
        # No rank insights section
        assert "RANK EVALUATION INSIGHTS" not in messages[1]["content"]

    def test_cover_letter_prompt_has_schema(self, candidate, job):
        from app.services.apply_json import build_json_cover_letter_prompt

        messages = build_json_cover_letter_prompt(candidate, job)
        assert len(messages) == 2
        assert "CoverLetter" in messages[0]["content"]
        assert job.company in messages[1]["content"]

    def test_reviewer_prompt_has_only_cv_json(self, candidate, job, evaluation):
        """Reviewer receives ONLY the CV JSON — no drafter reasoning."""
        from app.services.apply_json import build_json_review_prompt

        cv_dict = ENLATADO_CV_DICT
        messages = build_json_review_prompt(cv_dict, candidate, job, evaluation)

        # The serialized CV JSON is present
        assert "Senior ML Engineer" in messages[1]["content"]
        assert "AI Corp" in messages[1]["content"]
        # Missing keywords from evaluation are included
        assert "Kubernetes" in messages[1]["content"]

        # CRITICAL: No drafter reasoning or self-evaluation should leak
        assert "XYZ_GUIDANCE" not in messages[1]["content"]
        assert "APPLY_GUARDRAIL" not in messages[1]["content"]
        assert "CRITICAL REVIEWER" in messages[0]["content"]

    def test_revise_prompt_includes_old_cv_and_feedback(self, candidate, job):
        from app.services.apply_json import build_json_revise_prompt

        review = ReviewFeedback(**ENLATADO_REVIEW_DICT)
        messages = build_json_revise_prompt(ENLATADO_CV_DICT, review, candidate, job)

        assert len(messages) == 2
        # Old CV is included
        assert "AI Corp" in messages[1]["content"]
        # Reviewer feedback is included
        assert "Kubernetes" in messages[1]["content"]
        assert "SKILLED EDITOR" in messages[0]["content"]


# ═══════════════════════════════════════════════════════════════════════
# 2. Sanitizer integration tests
# ═══════════════════════════════════════════════════════════════════════


class TestSanitizerIntegration:
    """Sanitize-LLM-response is called before Pydantic validation."""

    @patch("app.services.apply_json.llm_completion")
    async def test_valid_json_passes_through(self, mock_llm, candidate, job):
        from app.services.apply_json import generate_cv

        # LLM returns valid JSON matching GenerateCVOutput schema
        mock_llm.return_value = json.dumps(ENLATADO_OUTPUT_DICT)

        result = await generate_cv(candidate, job, provider_config={"provider": "openai", "model": "gpt-4"})

        assert isinstance(result, GenerateCVOutput)
        assert result.cv.first_name == "Test"
        assert len(result.cv.experience) == 2

    @patch("app.services.apply_json.llm_completion")
    async def test_malformed_json_raises_lm_error(self, mock_llm, candidate, job):
        from app.exceptions import LLMError
        from app.services.apply_json import generate_cv

        # LLM returns garbage — sanitizer ValueError is wrapped into LLMError
        mock_llm.return_value = "{invalid json"

        with pytest.raises(LLMError, match="could not be parsed"):
            await generate_cv(candidate, job, provider_config={"provider": "openai", "model": "gpt-4"})

    @patch("app.services.apply_json.sanitize_llm_response")
    @patch("app.services.apply_json.llm_completion")
    async def test_sanitizer_is_called(
        self, mock_llm, mock_sanitize, candidate, job
    ):
        from app.services.apply_json import generate_cv

        mock_llm.return_value = json.dumps(ENLATADO_OUTPUT_DICT)
        mock_sanitize.return_value = ENLATADO_OUTPUT_DICT

        await generate_cv(candidate, job, provider_config={"provider": "openai", "model": "gpt-4"})

        mock_sanitize.assert_called_once()
        args, _ = mock_sanitize.call_args
        assert args[0] == mock_llm.return_value  # raw LLM output
        assert args[1] == "GenerateCVOutput"  # schema name

    @patch("app.services.apply_json.llm_completion")
    async def test_cover_letter_failure_returns_none(self, mock_llm, candidate, job):
        from app.services.apply_json import generate_cover_letter

        mock_llm.return_value = "very broken json"

        result = await generate_cover_letter(candidate, job, provider_config={"provider": "openai", "model": "gpt-4"})
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 3. Generation function round-trip tests
# ═══════════════════════════════════════════════════════════════════════


class TestGenerationFunctions:
    """Round-trip: mocked LLM → valid Pydantic output."""

    @patch("app.services.apply_json.llm_completion")
    async def test_generate_cv_round_trip(self, mock_llm, candidate, job, evaluation):
        from app.services.apply_json import generate_cv

        mock_llm.return_value = json.dumps(ENLATADO_OUTPUT_DICT)

        result = await generate_cv(candidate, job, evaluation, provider_config={"provider": "openai"})

        assert isinstance(result, GenerateCVOutput)
        assert isinstance(result.cv, CV)
        assert isinstance(result.metadata, CVMetadata)
        assert len(result.metadata.incorporated_keywords) == 2

    @patch("app.services.apply_json.llm_completion")
    async def test_generate_review_round_trip(self, mock_llm, candidate, job):
        from app.services.apply_json import generate_review

        mock_llm.return_value = json.dumps(ENLATADO_REVIEW_DICT)

        result = await generate_review(ENLATADO_CV_DICT, candidate, job, provider_config={"provider": "openai"})

        assert isinstance(result, ReviewFeedback)
        assert len(result.issues) == 1
        assert result.issues[0].type == "missing_keyword"

    @patch("app.services.apply_json.llm_completion")
    async def test_revise_round_trip(self, mock_llm, candidate, job):
        from app.services.apply_json import generate_revision

        review = ReviewFeedback(**ENLATADO_REVIEW_DICT)
        mock_llm.return_value = json.dumps(ENLATADO_OUTPUT_DICT)

        result = await generate_revision(
            ENLATADO_CV_DICT, review, candidate, job,
            provider_config={"provider": "openai"},
        )

        assert isinstance(result, GenerateCVOutput)
        assert isinstance(result.cv, CV)

    @patch("app.services.apply_json.llm_completion")
    async def test_cover_letter_round_trip(self, mock_llm, candidate, job):
        from app.schemas.cv import CoverLetter
        from app.services.apply_json import generate_cover_letter

        cl_dict = {
            "opening_paragraph": "Dear Hiring Manager,",
            "body_paragraphs": ["I have extensive NLP experience."],
            "company_connection_paragraph": "I admire TechCorp's work in AI.",
            "closing_paragraph": "Looking forward to discussing.",
        }
        mock_llm.return_value = json.dumps(cl_dict)

        result = await generate_cover_letter(candidate, job, provider_config={"provider": "openai"})

        assert isinstance(result, CoverLetter)
        assert "NLP" in result.body_paragraphs[0]


# ═══════════════════════════════════════════════════════════════════════
# 4. Fresh-context assertion for reviewer
# ═══════════════════════════════════════════════════════════════════════


class TestReviewerFreshContext:
    """Reviewer must evaluate the CV independently — no drafter reasoning."""

    def test_reviewer_prompt_omits_drafter_instructions(self, candidate, job):
        from app.services.apply_json import build_json_review_prompt

        messages = build_json_review_prompt(ENLATADO_CV_DICT, candidate, job)

        system = messages[0]["content"]
        user = messages[1]["content"]

        # Drafter-specific instructions must NOT appear in reviewer context
        drafter_fingerprints = [
            "X-Y-Z formula for every experience bullet",
            "APPLY_GUARDRAIL",
            "candidate's CV and cover letter",
            "Tailoring a candidate's CV",
            "Your role is to",
            "REFRAME existing experience",
        ]
        for fp in drafter_fingerprints:
            assert fp not in user, f"Drafter fingerprint leaked into reviewer user prompt: {fp}"
            assert fp not in system, f"Drafter fingerprint leaked into reviewer system prompt: {fp}"

    def test_reviewer_prompt_contains_cv_json(self, candidate, job):
        """The reviewer should see the actual CV JSON content, not the drafter's intent."""
        from app.services.apply_json import build_json_review_prompt

        messages = build_json_review_prompt(ENLATADO_CV_DICT, candidate, job)
        user = messages[1]["content"]

        # CV data is present
        assert '"first_name": "Test"' in user
        assert '"AI Corp"' in user

        # Reviewer-specific guardrail present
        assert "CRITICAL REVIEWER" in messages[0]["content"]


# ═══════════════════════════════════════════════════════════════════════
# 5. Typst compile from enlatado GenerateCVOutput
# ═══════════════════════════════════════════════════════════════════════


class TestTypstCompileEnlatado:
    """A GenerateCVOutput enlatado renders to PDF through the Typst path.

    Reuses the 1.2 infrastructure already verified on 5 examples.
    This test confirms the compile_cv function accepts the dict format
    produced by the generation pipeline.
    """

    def test_compile_cv_from_generatecvoutput_dict(self):
        from app.services.pdf_compiler_typst import compile_cv

        result = compile_cv(ENLATADO_OUTPUT_DICT)
        assert result is not None
        assert len(result) > 1000  # PDF should be non-trivial
        assert result[:4] == b"%PDF"

    def test_compile_cv_from_cv_only_dict(self):
        """Pass just the CV dict (no metadata wrapper)."""
        from app.services.pdf_compiler_typst import compile_cv

        result = compile_cv(ENLATADO_CV_DICT)
        assert result is not None
        assert result[:4] == b"%PDF"

    def test_compile_cv_to_file(self, tmp_path):
        from app.services.pdf_compiler_typst import compile_cv

        output = tmp_path / "test_cv.pdf"
        compile_cv(ENLATADO_OUTPUT_DICT, output=output)
        assert output.exists()
        assert output.stat().st_size > 1000
