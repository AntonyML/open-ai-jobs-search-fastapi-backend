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
# 6. Sanitizer array-to-scalar tests
# ═══════════════════════════════════════════════════════════════════════


class TestSanitizerArrayToScalar:
    """Sanitizer converts single-element arrays to scalars for string fields.

    The LLM sometimes returns ``["2024"]`` instead of ``"2024"`` for fields
    like ``period``, ``year``, ``url``.  The sanitizer must unwrap these
    before Pydantic validation.
    """

    def test_single_element_array_to_scalar(self):
        """["2024"] → "2024" for string fields like period, year, url."""
        from app.services.orchestrator.llm_response_sanitizer import sanitize_llm_response

        # Simulate the raw LLM output (must be a string)
        dirty_json = json.dumps({
            "cv": {
                "education": [{
                    "degree": "BS Computer Science",
                    "institution": "UCR",
                    "period": ["2015-2019"],  # array instead of string
                    "date_range": None,
                }],
                "certifications": [{
                    "name": "AWS Solutions Architect",
                    "issuer": "Amazon",
                    "year": ["2024"],  # array instead of string
                }],
            },
        })

        result = sanitize_llm_response(dirty_json, "GenerateCVOutput")
        cv = result["cv"]
        assert cv["education"][0]["period"] == "2015-2019"
        assert cv["certifications"][0]["year"] == "2024"

    def test_multi_element_array_joined(self):
        """["Python", "Java"] in a string field → "Python, Java"."""
        from app.services.orchestrator.llm_response_sanitizer import sanitize_llm_response

        dirty_json = json.dumps({
            "cv": {
                "education": [{
                    "institution": ["University X", "Technical Institute Y"],
                    "degree": "Dual Degree",
                    "period": None,
                    "date_range": None,
                }],
            },
        })

        result = sanitize_llm_response(dirty_json, "GenerateCVOutput")
        assert result["cv"]["education"][0]["institution"] == "University X, Technical Institute Y"


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


# ═══════════════════════════════════════════════════════════════════════
# 6. Adapt-flow prompt quality (recruiter analysis + drafter)
# ═══════════════════════════════════════════════════════════════════════


class TestAdaptFlowPrompts:
    """The adapt/personalize flow must ask for exactly 5 keywords / 3 red
    flags (matching the TikTok flow) and must NOT produce a cover letter."""

    def test_recruiter_analysis_prompt_asks_exact_counts(self, candidate):
        from app.services.apply_json import build_recruiter_analysis_prompt

        messages = build_recruiter_analysis_prompt(
            candidate, "Senior ML Engineer role with NLP and Kubernetes."
        )
        user = messages[1]["content"]
        assert "EXACTLY 5" in user
        assert "EXACTLY 3" in user

    def test_adapt_analysis_prompt_asks_exact_counts(self, candidate, job):
        from app.services.apply_json import build_adapt_analysis_prompt

        messages = build_adapt_analysis_prompt(
            candidate, {"cv": {"first_name": "Test"}}, job
        )
        user = messages[1]["content"]
        assert "EXACTLY 5" in user
        assert "EXACTLY 3" in user

    def test_adapt_url_analysis_prompt_asks_exact_counts(self, candidate):
        from app.services.apply_json import build_adapt_url_analysis_prompt

        messages = build_adapt_url_analysis_prompt(
            candidate, {"cv": {"first_name": "Test"}}, "https://example.com/job"
        )
        user = messages[1]["content"]
        assert "EXACTLY 5" in user
        assert "EXACTLY 3" in user

    def test_personalize_drafter_no_cover_letter_and_no_expansion(self, candidate):
        from app.schemas.cv import CVAnalysis
        from app.services.apply_json import build_personalize_drafter_prompt

        analysis = CVAnalysis(match_score=70, missing_keywords=["K8s"], red_flags=[])
        messages = build_personalize_drafter_prompt(
            candidate, "Senior ML Engineer role.", analysis
        )
        user = messages[1]["content"]
        assert "Do NOT include a cover letter" in user
        assert "Do NOT expand" in user

    def test_adapt_drafter_no_cover_letter_and_preserves_structure(self, candidate, job):
        from app.schemas.cv import CVAnalysis
        from app.services.apply_json import build_adapt_drafter_prompt

        analysis = CVAnalysis(match_score=70, missing_keywords=["K8s"], red_flags=[])
        messages = build_adapt_drafter_prompt(
            candidate, {"cv": {"first_name": "Test"}}, job, analysis
        )
        user = messages[1]["content"]
        assert "Do NOT include a cover letter" in user
        assert "PRESERVE the base CV" in user
        assert "ONE page" in user

    def test_adapt_url_drafter_no_cover_letter(self, candidate):
        from app.schemas.cv import CVAnalysis
        from app.services.apply_json import build_adapt_url_drafter_prompt

        analysis = CVAnalysis(match_score=70, missing_keywords=["K8s"], red_flags=[])
        messages = build_adapt_url_drafter_prompt(
            candidate, {"cv": {"first_name": "Test"}}, "https://example.com/job", analysis
        )
        user = messages[1]["content"]
        assert "Do NOT include a cover letter" in user
        assert "ONE page" in user

    async def test_personalize_cv_llm_drops_cover_letter(self, candidate, monkeypatch):
        """Even if the LLM sneaks in a cover letter, it is stripped before return."""
        from app.services import apply_json

        analysis = {"match_score": 70, "missing_keywords": [], "red_flags": [],
                    "adapted_experience": []}
        output = {"cv": {"first_name": "Test", "cover_letter": {
            "opening_paragraph": "Hi", "body_paragraphs": ["x"], "closing_paragraph": "bye",
        }}}

        async def fake_llm_json(messages, schema_type, provider_config, **kwargs):
            if schema_type.__name__ == "CVAnalysis":
                return analysis
            return output

        monkeypatch.setattr(apply_json, "_llm_json", fake_llm_json)
        _, out = await apply_json.personalize_cv_llm(
            candidate, "Senior ML Engineer role."
        )
        assert out["cv"].get("cover_letter") is None

    async def test_adapt_cv_llm_drops_cover_letter(self, candidate, job, monkeypatch):
        from app.services import apply_json

        analysis = {"match_score": 70, "missing_keywords": [], "red_flags": [],
                    "adapted_experience": []}
        output = {"cv": {"first_name": "Test", "cover_letter": {
            "opening_paragraph": "Hi", "body_paragraphs": ["x"], "closing_paragraph": "bye",
        }}}

        async def fake_llm_json(messages, schema_type, provider_config, **kwargs):
            if schema_type.__name__ == "CVAnalysis":
                return analysis
            return output

        monkeypatch.setattr(apply_json, "_llm_json", fake_llm_json)
        _, out = await apply_json.adapt_cv_llm(
            candidate, {"cv": {"first_name": "Test"}}, job
        )
        assert out["cv"].get("cover_letter") is None
