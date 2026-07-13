"""Tests for the expand service.

Uses an in-memory SQLite database and mocks the LLM calls and web searches.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    CandidateProfile,
    CompetencyExpansion,
    User,
)
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.services import expand


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


# ── Helper: mock LLM ────────────────────────────────────────────────


def mock_enriched_competencies():
    """Mock enriched competencies output from LLM."""
    return expand.EnrichedCompetenciesLLMOutput(
        enrichments=[
            expand.EnrichedCompetency(
                experience_item_id="cv_0",
                competencies=[
                    "TensorRT optimization",
                    "ML pipeline architecture",
                    "Distributed training",
                    "Model serving",
                    "Performance profiling",
                ],
            ),
            expand.EnrichedCompetency(
                experience_item_id="cv_1",
                competencies=[
                    "Collaborative filtering",
                    "A/B testing",
                    "Recommendation systems",
                    "Research methodology",
                    "Academic writing",
                ],
            ),
            expand.EnrichedCompetency(
                experience_item_id="li_0",
                competencies=[
                    "Kubernetes",
                    "Docker",
                    "CI/CD pipelines",
                    "Cloud infrastructure",
                ],
            ),
        ]
    )


def mock_proposed_additions():
    """Mock proposed additions output from LLM."""
    return expand.ProposedAdditionsLLMOutput(
        additions=[
            expand.ProposedAddition(
                category="skills.programming_ml",
                item={"language": "TensorRT", "proficiency": "Advanced", "frameworks": ["TensorRT", "ONNX Runtime"]},
                reason="Used for model optimization in production ML pipeline at Acme Corp",
            ),
            expand.ProposedAddition(
                category="skills.software_tools",
                item="Kubernetes",
                reason="Managed K8s clusters for ML model serving at Acme Corp",
            ),
            expand.ProposedAddition(
                category="skills.domain_expertise",
                item="MLOps",
                reason="Built and maintained production ML pipelines with CI/CD",
            ),
        ]
    )


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_expand_basic(db_session, sample_candidate):
    """execute_expand runs a full expansion and returns results."""
    with patch("app.services.expand.llm_completion_structured") as mock_llm:
        # Mock all LLM calls in sequence
        mock_llm.side_effect = [
            mock_enriched_competencies(),  # enrichment
            mock_proposed_additions(),     # proposed additions
        ]

        with patch("app.services.expand._scan_cv_folder", return_value=[]):
            with patch("app.services.expand._scan_linkedin_folder", return_value=[]):
                with patch("app.services.expand._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.expand._scan_references_folder", return_value=[]):
                        with patch("app.services.expand._scan_github_profile", return_value=[]):
                            with patch("app.services.expand._scan_other_urls", return_value=[]):
                                expansion = await expand.execute_expand(
                                    db=db_session,
                                    user_id="test-user-id",
                                    scan_cv=True,
                                    scan_linkedin=True,
                                    scan_diplomas=True,
                                    scan_references=True,
                                    scan_github=True,
                                    scan_other_urls=True,
                                )

    assert expansion.id is not None
    assert expansion.user_id == "test-user-id"
    assert expansion.candidate_id == sample_candidate.id
    assert expansion.status == "completed"
    assert expansion.scanned_cv is True
    assert expansion.scanned_linkedin is True
    assert expansion.scanned_diplomas is True
    assert expansion.scanned_references is True
    assert expansion.scanned_github is True
    assert expansion.scanned_other_urls is True


@pytest.mark.asyncio
async def test_execute_expand_profile_incomplete(db_session):
    """execute_expand raises ProfileIncompleteError when candidate profile missing."""
    with pytest.raises(ProfileIncompleteError):
        await expand.execute_expand(
            db=db_session,
            user_id="nonexistent-user",
            scan_cv=True,
        )


@pytest.mark.asyncio
async def test_execute_expand_llm_error(db_session, sample_candidate):
    """execute_expand raises LLMError when LLM call fails."""
    with patch("app.services.expand.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = LLMError("LLM timeout")

        with patch("app.services.expand._scan_cv_folder", return_value=[{"id": "cv_0", "title": "Test"}]):
            with patch("app.services.expand._scan_linkedin_folder", return_value=[]):
                with patch("app.services.expand._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.expand._scan_references_folder", return_value=[]):
                        with patch("app.services.expand._scan_github_profile", return_value=[]):
                            with patch("app.services.expand._scan_other_urls", return_value=[]):
                                with pytest.raises(LLMError):
                                    await expand.execute_expand(
                                        db=db_session,
                                        user_id="test-user-id",
                                        scan_cv=True,
                                    )


@pytest.mark.asyncio
async def test_execute_expand_with_experience_items(db_session, sample_candidate):
    """execute_expand processes experience items and enriches them."""
    with patch("app.services.expand.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_enriched_competencies(),
            mock_proposed_additions(),
        ]

        with patch("app.services.expand._scan_cv_folder", return_value=[
            {"id": "cv_0", "source": "cv", "type": "job_bullet", "title": "Senior ML Engineer", "description": "Built ML pipeline", "date": "2020-01", "source_file": "cv.pdf"},
        ]):
            with patch("app.services.expand._scan_linkedin_folder", return_value=[]):
                with patch("app.services.expand._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.expand._scan_references_folder", return_value=[]):
                        with patch("app.services.expand._scan_github_profile", return_value=[]):
                            with patch("app.services.expand._scan_other_urls", return_value=[]):
                                expansion = await expand.execute_expand(
                                    db=db_session,
                                    user_id="test-user-id",
                                    scan_cv=True,
                                )

    assert expansion.experience_items is not None
    assert len(expansion.experience_items) == 1
    assert expansion.experience_items[0]["id"] == "cv_0"
    assert expansion.enriched_competencies is not None
    assert len(expansion.enriched_competencies) == 1
    assert "TensorRT optimization" in expansion.enriched_competencies[0]["competencies"]


@pytest.mark.asyncio
async def test_execute_expand_proposes_additions(db_session, sample_candidate):
    """execute_expand proposes profile additions based on enriched competencies."""
    with patch("app.services.expand.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_enriched_competencies(),
            mock_proposed_additions(),
        ]

        with patch("app.services.expand._scan_cv_folder", return_value=[
            {"id": "cv_0", "source": "cv", "type": "job_bullet", "title": "Senior ML Engineer", "description": "Built ML pipeline with TensorRT", "date": "2020-01", "source_file": "cv.pdf"},
        ]):
            with patch("app.services.expand._scan_linkedin_folder", return_value=[]):
                with patch("app.services.expand._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.expand._scan_references_folder", return_value=[]):
                        with patch("app.services.expand._scan_github_profile", return_value=[]):
                            with patch("app.services.expand._scan_other_urls", return_value=[]):
                                expansion = await expand.execute_expand(
                                    db=db_session,
                                    user_id="test-user-id",
                                    scan_cv=True,
                                )

    assert expansion.proposed_additions is not None
    assert len(expansion.proposed_additions) == 3
    # Check that proposed additions reference the right categories
    categories = [a["category"] for a in expansion.proposed_additions]
    assert "skills.programming_ml" in categories
    assert "skills.software_tools" in categories
    assert "skills.domain_expertise" in categories


@pytest.mark.asyncio
async def test_get_expansion(db_session, sample_candidate):
    """get_expansion returns the expansion by ID."""
    with patch("app.services.expand.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_enriched_competencies(),
            mock_proposed_additions(),
        ]

        with patch("app.services.expand._scan_cv_folder", return_value=[]):
            with patch("app.services.expand._scan_linkedin_folder", return_value=[]):
                with patch("app.services.expand._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.expand._scan_references_folder", return_value=[]):
                        with patch("app.services.expand._scan_github_profile", return_value=[]):
                            with patch("app.services.expand._scan_other_urls", return_value=[]):
                                created = await expand.execute_expand(
                                    db=db_session,
                                    user_id="test-user-id",
                                    scan_cv=True,
                                )

    fetched = await expand.get_expansion(db_session, created.id, "test-user-id")
    assert fetched.id == created.id
    assert fetched.status == "completed"


@pytest.mark.asyncio
async def test_get_expansion_not_found(db_session):
    """get_expansion raises NotFoundError for non-existent expansion."""
    with pytest.raises(NotFoundError):
        await expand.get_expansion(db_session, "nonexistent-id", "test-user-id")


@pytest.mark.asyncio
async def test_get_expansion_wrong_user(db_session, sample_candidate):
    """get_expansion raises NotFoundError when expansion belongs to another user."""
    with patch("app.services.expand.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_enriched_competencies(),
            mock_proposed_additions(),
        ]

        with patch("app.services.expand._scan_cv_folder", return_value=[]):
            with patch("app.services.expand._scan_linkedin_folder", return_value=[]):
                with patch("app.services.expand._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.expand._scan_references_folder", return_value=[]):
                        with patch("app.services.expand._scan_github_profile", return_value=[]):
                            with patch("app.services.expand._scan_other_urls", return_value=[]):
                                created = await expand.execute_expand(
                                    db=db_session,
                                    user_id="test-user-id",
                                    scan_cv=True,
                                )

    with pytest.raises(NotFoundError):
        await expand.get_expansion(db_session, created.id, "other-user-id")


@pytest.mark.asyncio
async def test_list_expansions(db_session, sample_candidate):
    """list_expansions returns expansions for the user."""
    with patch("app.services.expand.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_enriched_competencies(),
            mock_proposed_additions(),
        ]

        with patch("app.services.expand._scan_cv_folder", return_value=[]):
            with patch("app.services.expand._scan_linkedin_folder", return_value=[]):
                with patch("app.services.expand._scan_diplomas_folder", return_value=[]):
                    with patch("app.services.expand._scan_references_folder", return_value=[]):
                        with patch("app.services.expand._scan_github_profile", return_value=[]):
                            with patch("app.services.expand._scan_other_urls", return_value=[]):
                                # Create 3 expansions
                                for i in range(3):
                                    await expand.execute_expand(
                                        db=db_session,
                                        user_id="test-user-id",
                                        scan_cv=True,
                                    )

    expansions = await expand.list_expansions(db_session, "test-user-id", limit=10)
    assert len(expansions) == 3


# ── Scanner function tests (mocked) ─────────────────────────────────


@pytest.mark.asyncio
async def test_scan_cv_folder():
    """_scan_cv_folder returns experience items from CV documents."""
    with patch("pathlib.Path.glob") as mock_glob:
        mock_glob.return_value = [Path("cv/test.pdf")]
        with patch("app.services.expand._extract_text_from_pdf", return_value="Senior ML Engineer at Acme Corp. Built ML pipeline."):
            items = expand._scan_cv_folder()

    assert isinstance(items, list)
    # Should return list of experience items


@pytest.mark.asyncio
async def test_scan_linkedin_folder():
    """_scan_linkedin_folder returns experience items from LinkedIn export."""
    with patch("pathlib.Path.glob") as mock_glob:
        mock_glob.return_value = [Path("linkedin/export.json")]
        with patch("builtins.open", mock_open(read_data='{"positions": [{"title": "ML Engineer", "company": "Acme", "description": "Built pipelines"}]}')):
            items = expand._scan_linkedin_folder()

    assert isinstance(items, list)


@pytest.mark.asyncio
async def test_scan_diplomas_folder():
    """_scan_diplomas_folder returns experience items from diplomas."""
    with patch("pathlib.Path.glob") as mock_glob:
        mock_glob.return_value = [Path("diplomas/diploma.pdf")]
        with patch("app.services.expand._extract_text_from_pdf", return_value="MSc Computer Science, DTU, 2020. Thesis: Efficient Transformers."):
            items = expand._scan_diplomas_folder()

    assert isinstance(items, list)


@pytest.mark.asyncio
async def test_scan_references_folder():
    """_scan_references_folder returns experience items from reference letters."""
    with patch("pathlib.Path.glob") as mock_glob:
        mock_glob.return_value = [Path("references/ref.pdf")]
        with patch("app.services.expand._extract_text_from_pdf", return_value="Reference for Jane Doe. She led ML team of 5."):
            items = expand._scan_references_folder()

    assert isinstance(items, list)


@pytest.mark.asyncio
async def test_scan_github_profile():
    """_scan_github_profile returns experience items from GitHub repos."""
    with patch("app.services.expand._fetch_github_repos") as mock_fetch:
        mock_fetch.return_value = [
            {"name": "ml-lib", "description": "ML library", "language": "Python", "topics": ["machine-learning", "pytorch"]},
            {"name": "cv-tool", "description": "CV tool", "language": "Python", "topics": ["computer-vision"]},
        ]
        items = expand._scan_github_profile("janedoe")

    assert isinstance(items, list)
    assert len(items) == 2


@pytest.mark.asyncio
async def test_scan_other_urls():
    """_scan_other_urls returns experience items from other URLs in profile."""
    from app.db.models import CandidateProfile
    candidate = CandidateProfile(
        id="test-candidate-id",
        user_id="test-user-id",
        full_name="Jane Doe",
        linkedin_url="https://linkedin.com/in/janedoe",
        github_url="https://github.com/janedoe",
    )
    items = expand._scan_other_urls(candidate)
    assert isinstance(items, list)


# ── Prompt builder tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_competency_enrichment_prompt():
    """build_competency_enrichment_prompt creates correct prompt structure."""
    items = [
        {"id": "cv_0", "source": "cv", "type": "job_bullet", "title": "Senior ML Engineer", "description": "Built ML pipeline with TensorRT", "date": "2020-01", "source_file": "cv.pdf"},
        {"id": "li_0", "source": "linkedin", "type": "certification", "title": "AWS Certified ML", "description": "AWS ML certification", "date": "2021-06", "source_file": "linkedin.json"},
    ]
    messages = expand.build_competency_enrichment_prompt(items)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "TensorRT" in messages[0]["content"]
    assert "AWS Certified ML" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_proposed_additions_prompt(sample_candidate):
    """build_proposed_additions_prompt creates correct prompt structure."""
    enriched = [
        expand.EnrichedCompetency(
            experience_item_id="cv_0",
            competencies=["TensorRT optimization", "ML pipeline architecture", "Distributed training"],
        ),
    ]
    messages = expand.build_proposed_additions_prompt(sample_candidate, enriched)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "GUARDRAIL" in messages[0]["content"]
    assert "Jane Doe" in messages[0]["content"]
    assert "TensorRT" in messages[0]["content"]


# ── Schema validation tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_expand_request_schema():
    """ExpandRequest schema validates required fields."""
    payload = expand.ExpandRequest(
        scan_cv=True,
        scan_linkedin=True,
        scan_diplomas=True,
        scan_references=True,
        scan_github=True,
        scan_other_urls=True,
    )
    assert payload.scan_cv is True
    assert payload.scan_linkedin is True


@pytest.mark.asyncio
async def test_expand_request_schema_defaults():
    """ExpandRequest schema has correct defaults."""
    payload = expand.ExpandRequest()
    assert payload.scan_cv is True
    assert payload.scan_linkedin is True
    assert payload.scan_diplomas is True
    assert payload.scan_references is True
    assert payload.scan_github is True
    assert payload.scan_other_urls is True


# ── LaTeX compilation tests (mocked) ────────────────────────────────


@pytest.mark.asyncio
async def test_compile_latex_success():
    """compile_latex returns PDF path and page count on success."""
    import tempfile
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = mock_proc

        with patch("app.services.apply._get_pdf_page_count", return_value=2):
            with patch("pathlib.Path.exists", return_value=True):
                pdf_path, pages = await expand.compile_latex(
                    "dummy tex content",
                    Path(tempfile.gettempdir()),
                    "test_cv",
                    "lualatex",
                    2,
                )

    assert pages == 2
    assert pdf_path.name == "test_cv.pdf"


@pytest.mark.asyncio
async def test_compile_latex_failure():
    """compile_latex raises LatexCompileError on compilation failure."""
    import tempfile
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: missing font"))
        mock_exec.return_value = mock_proc

        with pytest.raises(expand.LatexCompileError):
            await expand.compile_latex(
                "dummy tex content",
                Path(tempfile.gettempdir()),
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

        with patch("app.services.expand._get_pdf_page_count", return_value=3):  # Expected 2, got 3
            with patch("pathlib.Path.exists", return_value=True):
                with pytest.raises(expand.LatexCompileError):
                    await expand.compile_latex(
                        "dummy tex content",
                        Path(tempfile.gettempdir()),
                        "test_cv",
                        "lualatex",
                        2,
                    )
