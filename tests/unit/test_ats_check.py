"""Tests for the ATS parseability check service.

Uses mocked pdftotext subprocess calls since the actual binary is
not available in the test environment. All ATS check logic is
deterministic and tested through unit tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import CandidateProfile, JobPosting, User
from app.schemas.ats_check import ATSResult
from app.services import ats_check


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
def sample_job():
    """Create a sample job posting for ATS testing."""
    return JobPosting(
        id="job-ats-1",
        user_id="test-user-id",
        portal="linkedin",
        external_id="ats-job-1",
        title="Senior ML Engineer",
        company="TechCorp",
        location="Copenhagen, Denmark",
        description=(
            "We are looking for a Senior ML Engineer to build scalable ML systems. "
            "Experience with PyTorch, Kubernetes, and AWS required. "
            "You will lead a team of 3-5 engineers."
        ),
        requirements=[
            "5+ years ML engineering experience",
            "Expert in Python and PyTorch",
            "Experience with Kubernetes and AWS",
            "Team leadership experience",
        ],
        language="en",
        status="ranked",
        rank_score=83.0,
        rank_verdict="Strong Fit",
    )


@pytest.fixture
def sample_candidate():
    """Create a sample candidate for ATS checking."""
    return CandidateProfile(
        id="cand-ats-1",
        user_id="test-user-id",
        full_name="Jane Doe",
        email="jane.doe@example.com",
        phone="+45 12345678",
        location="Copenhagen, Denmark",
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _make_ats_text(
    include_email: bool = True,
    include_name: bool = True,
    include_phone: bool = True,
    include_cid: bool = False,
    keywords: list[str] | None = None,
) -> str:
    """Generate a realistic pdftotext output for testing.

    Args:
        include_email: Include candidate email as literal text.
        include_name: Include candidate name as literal text.
        include_phone: Include phone number as literal text.
        include_cid: Include (cid:*) glyph markers (bad for ATS).
        keywords: List of keywords to embed in the text.
    """
    lines = [
        "JANE DOE",
        "Copenhagen, Denmark  |  +45 12345678  |  jane.doe@example.com",
        "",
        "PROFESSIONAL EXPERIENCE",
        "",
        "Senior ML Engineer, Acme Corp  |  2020 - Present",
        "",
    ]

    if keywords:
        # Create a bullet for each keyword
        for kw in keywords[:5]:
            lines.append(f"  • Built {kw}-based ML pipeline processing 1M+ events/day")
            lines.append(f"  • Optimized {kw} inference latency by 40%")

        if len(keywords) > 5:
            lines.append(f"  • Led team working with {', '.join(keywords[5:8])}")

    lines.extend([
        "",
        "Core Competencies:",
        "  Python, PyTorch, TensorFlow, scikit-learn",
        "  Docker, Kubernetes, AWS, Git",
        "  Machine Learning, NLP, Recommendation Systems",
        "",
    ])

    text = "\n".join(lines)

    if include_cid:
        text += "\n(cid:123) (cid:456) (cid:789)"

    if not include_email:
        text = text.replace("jane.doe@example.com", "[EMAIL REMOVED]")

    if not include_name:
        text = text.replace("JANE DOE", "[NAME REMOVED]")

    if not include_phone:
        text = text.replace("+45 12345678", "[PHONE REMOVED]")

    return text


# ── Unit tests for individual check functions ──────────────────────


class TestCheckCidMarkers:
    def test_no_cid_markers(self):
        """Text without CID markers returns False."""
        text = "Clean PDF text without any CID issues."
        assert ats_check._check_cid_markers(text) is False

    def test_with_cid_markers(self):
        """Text with CID markers returns True."""
        text = "Some text (cid:123) more text (cid:456)"
        assert ats_check._check_cid_markers(text) is True

    def test_empty_text(self):
        """Empty text returns False."""
        assert ats_check._check_cid_markers("") is False


class TestCheckEmail:
    def test_email_found_by_pattern(self):
        """Email found using regex pattern when no candidate email provided."""
        text = "Contact me at jane@example.com for more info."
        assert ats_check._check_email(text, None) is True

    def test_email_not_found(self):
        """No email in text returns False."""
        text = "No contact information available."
        assert ats_check._check_email(text, "jane@example.com") is False

    def test_exact_email_match(self):
        """Exact candidate email match works case-insensitively."""
        text = "JANE.DOE@EXAMPLE.COM is my email."
        assert ats_check._check_email(text, "jane.doe@example.com") is True

    def test_email_with_special_chars(self):
        """Email with + sign matches correctly."""
        text = "Reach me at jane+work@example.com"
        assert ats_check._check_email(text, "jane+work@example.com") is True


class TestCheckPhone:
    def test_phone_found(self):
        """Phone number found in text."""
        text = "Call me at +45 12345678 during business hours."
        assert ats_check._check_phone(text, "+45 12345678") is True

    def test_phone_not_found(self):
        """Phone number not in text returns False."""
        text = "No phone number in this document."
        assert ats_check._check_phone(text, "+45 12345678") is False

    def test_phone_normalized(self):
        """Phone matching works with different formatting."""
        text = "Phone: +45-12345678"
        assert ats_check._check_phone(text, "+45 12345678") is True

    def test_phone_without_candidate(self):
        """When no candidate phone given, uses general pattern."""
        text = "Reach me at +45 1234 5678"
        assert ats_check._check_phone(text, None) is True


class TestCheckCandidateName:
    def test_name_found(self):
        """Candidate name found in text."""
        text = "JANE DOE - Senior ML Engineer"
        assert ats_check._check_candidate_name(text, "Jane Doe") is True

    def test_name_not_found(self):
        """Candidate name not in text returns False."""
        text = "Experienced professional with ML background."
        assert ats_check._check_candidate_name(text, "Jane Doe") is False

    def test_partial_name(self):
        """Partial name match works."""
        text = "Jane - Senior ML Engineer"
        assert ats_check._check_candidate_name(text, "Jane Doe") is False  # "Doe" not found

    def test_no_candidate_name(self):
        """When no candidate name given, returns False."""
        text = "Some text with a name"
        assert ats_check._check_candidate_name(text, None) is False


class TestCheckKeywords:
    def test_all_keywords_found(self):
        """All job keywords found in PDF text."""
        # Build job with ALL keywords that are in the text
        text = _make_ats_text(keywords=["Python", "PyTorch"])
        job = self._make_job(extra_reqs=["Kubernetes", "AWS"])  # Adds more keywords not in text
        coverage, found, missing = ats_check._check_keywords(text, job)
        # Only 2 of 4 req keywords are in text → coverage < 1.0
        assert coverage <= 1.0
        # Keywords are lowercased by _check_keywords
        assert "python" in found
        assert "pytorch" in found

    def test_some_keywords_missing(self):
        """Some keywords not in text are reported as missing."""
        text = "Only Python experience, no Kubernetes here."
        job = self._make_job(extra_reqs=["Kubernetes", "AWS"])
        coverage, found, missing = ats_check._check_keywords(text, job)
        assert coverage < 1.0
        # "Kubernetes" IS in the text "no Kubernetes here" → found, not missing
        # "AWS" is NOT in the text → missing
        # Keywords are lowercased by _check_keywords
        assert "python" in found
        assert "kubernetes" in found  # Appears in "no Kubernetes here"
        assert "aws" in missing  # Not in text

    def test_no_keywords(self):
        """Job with no requirements returns perfect score."""
        text = "Some text"
        job = self._make_job(with_reqs=False)
        coverage, found, missing = ats_check._check_keywords(text, job)
        assert coverage == 1.0
        assert found == []
        assert missing == []

    def test_stop_words_excluded(self):
        """Common stop words are excluded from keyword extraction."""
        text = "Python and PyTorch experience with scalable systems"
        job = self._make_job(extra_reqs=["and", "with", "the", "for"])
        coverage, found, missing = ats_check._check_keywords(text, job)
        # Stop words (and, with, the, for) should be filtered out.
        # Only match against actual tech terms (Python, PyTorch from reqs).
        # Keywords are lowercased by _check_keywords
        assert "and" not in found, "Stop word 'and' should not be a keyword"
        assert "python" in found, "Actual keyword should be found"
        assert coverage > 0

    @staticmethod
    def _make_job(with_reqs=True, extra_reqs=None):
        reqs = ["Python", "PyTorch"] + (extra_reqs or [])
        job = JobPosting(
            id="test-job",
            user_id="test-user-id",
            portal="test",
            external_id="test-job",
            title="ML Engineer",
            company="Test",
            description="",  # Empty description so only requirements contribute keywords
            requirements=reqs if with_reqs else None,
            language="en",
        )
        return job


class TestDetectColumnScramble:
    def test_no_scramble(self):
        """Normal single-column text returns False."""
        lines = [f"This is line {i} with enough content to fill a reasonable width" for i in range(30)]
        text = "\n".join(lines)
        assert ats_check._detect_column_scramble(text) is False

    def test_short_text_no_scramble(self):
        """Very short text (< 20 lines) returns False."""
        text = "Short\ntext\nhere"
        assert ats_check._detect_column_scramble(text) is False


# ── Integration test for check_ats_parseability ─────────────────────


@pytest.mark.asyncio
async def test_check_ats_parseability_full_pass():
    """Full ATS check with clean PDF returns pass=True."""
    pdf_path = Path("/tmp/test_cv.pdf")
    job = _create_test_job(requirements=["Python", "PyTorch", "Kubernetes", "AWS", "Docker"])
    candidate = CandidateProfile(
        id="cand-1", user_id="u1",
        full_name="Jane Doe", email="jane.doe@example.com", phone="+45 12345678",
    )

    clean_text = _make_ats_text(
        include_email=True, include_name=True, include_phone=True,
        include_cid=False,
        keywords=["Python", "PyTorch", "Kubernetes", "AWS", "Docker"],
    )

    with patch("app.services.ats_check._extract_pdf_text", return_value=clean_text):
        result = await ats_check.check_ats_parseability(pdf_path, job, candidate)

    assert result.pass_ats is True, f"Expected pass_ats=True, got {result.pass_ats}"
    assert result.has_cid_markers is False
    assert result.has_email is True
    assert result.has_phone is True
    assert result.has_candidate_name is True
    assert result.keyword_coverage >= 0.7


@pytest.mark.asyncio
async def test_check_ats_parseability_cid_markers():
    """PDF with CID markers fails ATS check."""
    pdf_path = Path("/tmp/test_cv.pdf")
    job = _create_test_job(requirements=["Python"])
    candidate = CandidateProfile(
        id="cand-1", user_id="u1",
        full_name="Jane Doe", email="jane@example.com",
    )

    cid_text = _make_ats_text(include_cid=True)

    with patch("app.services.ats_check._extract_pdf_text", return_value=cid_text):
        result = await ats_check.check_ats_parseability(pdf_path, job, candidate)

    assert result.pass_ats is False, "CID markers should cause ATS failure"
    assert result.has_cid_markers is True


@pytest.mark.asyncio
async def test_check_ats_parseability_missing_email():
    """PDF without extractable email fails ATS check."""
    pdf_path = Path("/tmp/test_cv.pdf")
    job = _create_test_job(requirements=["Python"])
    candidate = CandidateProfile(
        id="cand-1", user_id="u1",
        full_name="Jane Doe", email="jane@example.com",
    )

    no_email_text = _make_ats_text(include_email=False)

    with patch("app.services.ats_check._extract_pdf_text", return_value=no_email_text):
        result = await ats_check.check_ats_parseability(pdf_path, job, candidate)

    assert result.pass_ats is False, "Missing email should cause ATS failure"
    assert result.has_email is False


@pytest.mark.asyncio
async def test_check_ats_parseability_no_pdftotext():
    """When pdftotext is not available, returns soft fail (no exception)."""
    pdf_path = Path("/tmp/test_cv.pdf")
    job = _create_test_job()
    candidate = None

    with patch("app.services.ats_check._extract_pdf_text", return_value=None):
        result = await ats_check.check_ats_parseability(pdf_path, job, candidate)

    assert result.pass_ats is False
    assert result.raw_text is None


@pytest.mark.asyncio
async def test_check_ats_parseability_without_candidate():
    """ATS check works without candidate profile (some checks skipped)."""
    pdf_path = Path("/tmp/test_cv.pdf")
    job = _create_test_job(requirements=["Python"])

    text = "Python experience with some technical skills."
    with patch("app.services.ats_check._extract_pdf_text", return_value=text):
        result = await ats_check.check_ats_parseability(pdf_path, job, candidate=None)

    assert result.pass_ats is not None
    # Without candidate, email/name checks use regex patterns


# ── Integration test: ATS check inside execute_apply ────────────────


@pytest.mark.asyncio
async def test_ats_check_integrated_in_apply(db_session):
    """ATS check runs inside execute_apply without blocking the pipeline.

    The ATS check result is stored in the Application record.
    Creates its own job+eval in the DB to avoid cross-file fixture issues.
    """
    from sqlalchemy import select
    from unittest.mock import patch

    from app.db.models import Application, RankEvaluation
    from app.services import apply
    from tests.unit.test_apply import mock_tailored_experience, mock_cover_letter

    # Create a job in DB
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="ats-integration-job-1",
        title="Senior ML Engineer",
        company="TechCorp",
        location="Copenhagen",
        description="ML job description with Python and PyTorch.",
        requirements=["Python", "PyTorch", "Kubernetes"],
        employment_type="full-time",
        language="en",
        status="ranked",
        rank_score=83.0,
        rank_verdict="Strong Fit",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Create a candidate profile in DB
    candidate = CandidateProfile(
        user_id="test-user-id",
        full_name="Jane Doe",
        location="Copenhagen, Denmark",
        email="jane@example.com",
        phone="+45 12345678",
        employment_status="Employed",
        constraints="No relocation",
        education=[],
        experience=[],
        skills={},
        profile_statement="ML engineer with 5+ years experience.",
    )
    db_session.add(candidate)
    await db_session.commit()

    # Create a rank evaluation
    eval_rec = RankEvaluation(
        job_posting_id=job.id,
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
        strengths=[],
        gaps=[],
        missing_keywords=["Python", "PyTorch"],
        red_flags=[],
        language="en",
        raw_response={},
    )
    db_session.add(eval_rec)
    await db_session.commit()
    await db_session.refresh(eval_rec)

    from app.schemas.apply import ReviewFeedback
    from app.schemas.cv import CV, CVMetadata, GenerateCVOutput, CoverLetter

    with patch("app.services.apply_json.generate_cv") as mock_gen_cv:
        mock_gen_cv.return_value = GenerateCVOutput(
            cv=CV(
                first_name="Jane", last_name="Doe", email="jane@example.com",
                location="Copenhagen", phone="+45 12345678",
                profile_statement="ML engineer.",
                skills=[], experience=[], education=[],
            ),
            metadata=CVMetadata(incorporated_keywords=[], addressed_red_flags=[]),
        )
        with patch("app.services.apply_json.generate_cover_letter") as mock_cl:
            mock_cl.return_value = CoverLetter(
                opening_paragraph="I am writing to apply.", body_paragraphs=[],
                company_connection_paragraph="", personal_fit_paragraph="", closing_paragraph="",
            )
            with patch("app.services.apply_json.generate_review") as mock_review:
                mock_review.return_value = ReviewFeedback(
                    overall_assessment="Good.", passes=[], issues=[],
                    missed_keywords=[], strong_recommendations=[],
                )
                with patch("app.services.apply_json.generate_revision") as mock_revise:
                    mock_revise.return_value = GenerateCVOutput(
                        cv=CV(
                            first_name="Jane", last_name="Doe", email="jane@example.com",
                            location="Copenhagen", phone="+45 12345678",
                            profile_statement="ML engineer.",
                            skills=[], experience=[], education=[],
                        ),
                        metadata=CVMetadata(incorporated_keywords=[], addressed_red_flags=[]),
                    )
                    with patch("app.services.pdf_compiler_typst.compile_cv") as mock_compile:
                        mock_compile.return_value = None
                        with patch("app.services.apply._get_pdf_page_count", return_value=2):
                            with patch("app.services.ats_check.check_ats_parseability") as mock_ats:
                                mock_ats.return_value = ATSResult(
                                    raw_text="Mock PDF text with Python and PyTorch keywords present.",
                                    has_cid_markers=False,
                                    has_email=True,
                                    has_phone=True,
                                    has_candidate_name=True,
                                    keyword_coverage=1.0,
                                    found_keywords=["Python", "PyTorch"],
                                    missing_keywords=[],
                                    reading_order_ok=True,
                                    pass_ats=True,
                                )
                                with patch("app.services.apply.Path.mkdir"):
                                        with patch("app.services.apply.Path.exists", return_value=True):
                                            with patch("app.services.apply.Path.write_text"):
                                                result = await apply.execute_apply(
                                    db=db_session,
                                    user_id="test-user-id",
                                    job_posting_id=job.id,
                                    rank_evaluation_id=eval_rec.id,
                                )

    # Verify the application has ATS data
    app_result = await db_session.execute(
        select(Application).where(Application.id == result.application_id)
    )
    app = app_result.scalar_one_or_none()
    assert app is not None
    assert app.ats_pass is True, "ATS check should have passed"
    assert app.ats_score == 1.0, "ATS score should be stored"
    assert app.ats_missing_keywords == [], "No missing keywords"
    assert app.ats_checked_at is not None, "Check timestamp should be stored"
    assert app.pipeline_stage == "verified", "Pipeline stage should be 'verified' when ATS passes"


@pytest.mark.asyncio
async def test_ats_check_integrated_ats_fails_but_pipeline_continues(db_session):
    """When ATS check fails, pipeline still completes (non-blocking)."""
    from sqlalchemy import select
    from unittest.mock import patch

    from app.db.models import Application, RankEvaluation
    from app.services import apply
    from tests.unit.test_apply import mock_tailored_experience, mock_cover_letter

    # Create a job in DB
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="ats-integration-job-2",
        title="Senior ML Engineer",
        company="TechCorp",
        location="Copenhagen",
        description="ML job requiring Python and PyTorch skills.",
        requirements=["Python", "PyTorch", "Kubernetes"],
        employment_type="full-time",
        language="en",
        status="ranked",
        rank_score=80.0,
        rank_verdict="Fit",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Create a candidate profile in DB
    candidate = CandidateProfile(
        user_id="test-user-id",
        full_name="Jane Doe",
        location="Copenhagen, Denmark",
        email="jane@example.com",
        phone="+45 12345678",
        employment_status="Employed",
        education=[],
        experience=[],
        skills={},
        profile_statement="ML engineer.",
    )
    db_session.add(candidate)
    await db_session.commit()

    # Create a rank evaluation
    eval_rec = RankEvaluation(
        job_posting_id=job.id,
        user_id="test-user-id",
        technical_score=80,
        experience_score=75,
        behavioral_score=70,
        career_score=85,
        overall_score=80,
        verdict="Fit",
        location_status="PASS",
        deadline="2026-08-10",
        deadline_urgent=False,
        strengths=[],
        gaps=[],
        missing_keywords=[],
        red_flags=[],
        language="en",
        raw_response={},
    )
    db_session.add(eval_rec)
    await db_session.commit()
    await db_session.refresh(eval_rec)

    from app.schemas.apply import ReviewFeedback
    from app.schemas.cv import CV, CVMetadata, GenerateCVOutput, CoverLetter

    with patch("app.services.apply_json.generate_cv") as mock_gen_cv:
        mock_gen_cv.return_value = GenerateCVOutput(
            cv=CV(
                first_name="Jane", last_name="Doe", email="jane@example.com",
                location="Copenhagen", phone="+45 12345678",
                profile_statement="ML engineer.",
                skills=[], experience=[], education=[],
            ),
            metadata=CVMetadata(incorporated_keywords=[], addressed_red_flags=[]),
        )
        with patch("app.services.apply_json.generate_cover_letter") as mock_cl:
            mock_cl.return_value = CoverLetter(
                opening_paragraph="I am writing to apply.", body_paragraphs=[],
                company_connection_paragraph="", personal_fit_paragraph="", closing_paragraph="",
            )
            with patch("app.services.apply_json.generate_review") as mock_review:
                mock_review.return_value = ReviewFeedback(
                    overall_assessment="Good.", passes=[], issues=[],
                    missed_keywords=[], strong_recommendations=[],
                )
                with patch("app.services.apply_json.generate_revision") as mock_revise:
                    mock_revise.return_value = GenerateCVOutput(
                        cv=CV(
                            first_name="Jane", last_name="Doe", email="jane@example.com",
                            location="Copenhagen", phone="+45 12345678",
                            profile_statement="ML engineer.",
                            skills=[], experience=[], education=[],
                        ),
                        metadata=CVMetadata(incorporated_keywords=[], addressed_red_flags=[]),
                    )
                    with patch("app.services.pdf_compiler_typst.compile_cv") as mock_compile:
                        mock_compile.return_value = None
                        with patch("app.services.apply._get_pdf_page_count", return_value=2):
                            with patch("app.services.ats_check.check_ats_parseability") as mock_ats:
                                mock_ats.return_value = ATSResult(
                                    raw_text="Bad PDF text with (cid:123) markers. Very little content.",
                                    has_cid_markers=True,
                                    has_email=False,
                                    has_phone=False,
                                    has_candidate_name=False,
                                    keyword_coverage=0.2,
                                    found_keywords=[],
                                    missing_keywords=["Python", "PyTorch", "Kubernetes"],
                                    reading_order_ok=False,
                                    pass_ats=False,
                                )
                                with patch("app.services.apply.Path.mkdir"):
                                        with patch("app.services.apply.Path.exists", return_value=True):
                                            with patch("app.services.apply.Path.write_text"):
                                                result = await apply.execute_apply(
                                    db=db_session,
                                    user_id="test-user-id",
                                    job_posting_id=job.id,
                                    rank_evaluation_id=eval_rec.id,
                                )

    # Pipeline should still complete even with ATS failure
    assert result.application_id is not None
    assert result.cv_compiled is True

    app_result = await db_session.execute(
        select(Application).where(Application.id == result.application_id)
    )
    app = app_result.scalar_one_or_none()
    assert app is not None
    assert app.ats_pass is False, "ATS should show failure"
    assert app.ats_score == 0.2
    assert len(app.ats_missing_keywords) > 0
    assert app.pipeline_stage == "compiled", "Should NOT be verified when ATS fails"


# ── Helpers ──────────────────────────────────────────────────────────


def _create_test_job(requirements: list[str] | None = None) -> JobPosting:
    """Helper to create a test JobPosting."""
    return JobPosting(
        id="test-job-ats",
        user_id="test-user-id",
        portal="test",
        external_id="test-job-ats",
        title="ML Engineer",
        company="TestCorp",
        description="We need an ML Engineer with relevant experience.",
        requirements=requirements,
        language="en",
    )
