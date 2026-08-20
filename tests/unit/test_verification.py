"""Tests for FASE 2 — Verification Checklist service.

Tests cover:
- All 6 deterministic content checks (on structured CV JSON)
- ATS-based checks (CID, contact, keywords)
- LLM content quality checks (with mocked LLM)
- Full integration test via run_verification_checklist
- Edge cases: None values, empty dicts, missing data
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.db.models import Application, CandidateProfile, JobPosting
from app.schemas.ats_check import ATSResult
from app.schemas.verification import LlmContentCheckOutput, VerificationCheck, VerificationResult
from app.services import verification

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def sample_candidate():
    return CandidateProfile(
        id="cand-1",
        user_id="user-1",
        full_name="Jane Doe",
        email="jane.doe@example.com",
        phone="+45 12345678",
        profile_statement="Senior ML Engineer with 5+ years experience in Python and PyTorch.",
        experience=[
            {"title": "ML Engineer", "company": "Acme Corp", "bullets": []},
        ],
        skills={
            "programming_ml": [{"language": "Python", "proficiency": "Expert"}],
            "domain_expertise": ["Machine Learning"],
        },
    )


@pytest.fixture
def sample_job():
    return JobPosting(
        id="job-1",
        user_id="user-1",
        portal="linkedin",
        external_id="job-1",
        title="Senior ML Engineer",
        company="TechCorp",
        description="ML job requiring Python and PyTorch skills.",
        requirements=["Python", "PyTorch", "Kubernetes"],
        language="en",
        status="ranked",
    )


@pytest.fixture
def sample_cv_json():
    """Structured CV JSON matching the app.schemas.cv.CV shape."""
    return {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane.doe@example.com",
        "phone": "+45 12345678",
        "profile_statement": "Senior ML Engineer with expertise in Python and PyTorch.",
        "experience": [
            {
                "title": "ML Engineer",
                "company": "Acme Corp",
                "date_range": {"start": "2020-01", "end": "2024-12"},
                "bullets": ["Built ML pipelines processing 1M events/day."],
            }
        ],
        "cover_letter": {
            "opening_paragraph": "Dear TechCorp Hiring Team,",
            "body_paragraphs": [
                "I am writing to apply for the Senior ML Engineer position.",
                "At Acme Corp, I built ML pipelines processing 1M events/day.",
                "TechCorp's mission in AI aligns with my career goals.",
            ],
            "company_connection_paragraph": "TechCorp's mission in AI aligns with my career goals.",
            "closing_paragraph": "Sincerely, Jane Doe",
        },
    }


@pytest.fixture
def sample_application(sample_cv_json):
    return Application(
        id="app-1",
        user_id="user-1",
        job_posting_id="job-1",
        rank_evaluation_id="eval-1",
        cv_pdf_path="/tmp/test_cv.pdf",
        cv_json=sample_cv_json,
        cv_compiled=True,
        cover_letter_compiled=True,
        stage="compiled",
        cv_template="moderncv-banking",
        cover_letter_template="cover-cls",
        language="en",
    )


@pytest.fixture
def sample_ats_pass():
    return ATSResult(
        raw_text="Jane Doe — Senior ML Engineer — jane.doe@example.com — +45 12345678",
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


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS: Individual deterministic checks
# ═══════════════════════════════════════════════════════════════════


class TestCheckNameInCV:
    def test_name_found(self):
        result = verification._check_name_in_cv({"first_name": "Jane", "last_name": "Doe"}, "Jane Doe")
        assert result.passed is True
        assert "Jane Doe" in result.details

    def test_name_not_found(self):
        result = verification._check_name_in_cv({"first_name": "John", "last_name": "Smith"}, "Jane Doe")
        assert result.passed is False
        assert "not found" in result.details.lower()

    def test_empty_cv(self):
        result = verification._check_name_in_cv({}, "Jane Doe")
        assert result.passed is False

    def test_no_candidate_name(self):
        result = verification._check_name_in_cv({"first_name": "Jane", "last_name": "Doe"}, None)
        assert result.passed is False

    def test_case_insensitive(self):
        result = verification._check_name_in_cv({"first_name": "JANE", "last_name": "DOE"}, "jane doe")
        assert result.passed is True

    def test_first_name_fallback(self):
        """When full name not matched, checks first name as fallback."""
        result = verification._check_name_in_cv({"first_name": "Jane", "last_name": ""}, "Jane Doe")
        assert result.passed is True
        assert "First name" in result.details


class TestCheckEmailInCV:
    def test_email_found(self):
        result = verification._check_email_in_cv({"email": "jane@example.com"}, "jane@example.com")
        assert result.passed is True

    def test_email_mismatch(self):
        result = verification._check_email_in_cv({"email": "other@example.com"}, "jane@example.com")
        assert result.passed is False

    def test_no_candidate_email(self):
        """When no candidate email given, CV email alone passes."""
        result = verification._check_email_in_cv({"email": "someone@example.com"}, None)
        assert result.passed is True

    def test_empty_cv(self):
        result = verification._check_email_in_cv({}, "jane@example.com")
        assert result.passed is False

    def test_missing_email_field(self):
        result = verification._check_email_in_cv({"first_name": "Jane"}, "jane@example.com")
        assert result.passed is False


class TestCheckRoleInProfile:
    def test_role_found_in_cv(self):
        result = verification._check_role_in_profile(
            {"profile_statement": "Senior ML Engineer with experience."}, "Senior ML Engineer", None
        )
        assert result.passed is True

    def test_role_not_found(self):
        result = verification._check_role_in_profile(
            {"profile_statement": "Data Scientist with experience."}, "Senior ML Engineer", None
        )
        assert result.passed is False

    def test_role_found_in_candidate_profile_statement(self):
        result = verification._check_role_in_profile(
            {"profile_statement": "Generic CV text."},
            "Senior ML Engineer",
            "I am a Senior ML Engineer with 5+ years experience.",
        )
        assert result.passed is True

    def test_no_job_title(self):
        result = verification._check_role_in_profile({"profile_statement": "Some text"}, None, "profile")
        assert result.passed is True  # Skipped gracefully

    def test_single_keyword_match(self):
        """Matches on a significant word from the job title."""
        result = verification._check_role_in_profile(
            {"profile_statement": "I love ML engineering."}, "Senior ML Engineer", None
        )
        assert result.passed is True


class TestCheckCompanyInCover:
    def test_company_found(self):
        result = verification._check_company_in_cover("I am excited to join TechCorp.", "TechCorp")
        assert result.passed is True

    def test_company_not_found(self):
        result = verification._check_company_in_cover("I am excited to join this company.", "TechCorp")
        assert result.passed is False

    def test_no_company(self):
        result = verification._check_company_in_cover("Cover letter text.", "Not specified")
        assert result.passed is True  # Skipped

    def test_empty_cover(self):
        result = verification._check_company_in_cover("", "TechCorp")
        assert result.passed is False

    def test_multi_word_company(self):
        result = verification._check_company_in_cover("I want to work at Google DeepMind.", "Google DeepMind")
        assert result.passed is True


class TestCheckDateFormat:
    def test_consistent_range(self):
        cv = {"experience": [{"date_range": {"start": "2020-01", "end": "2024-12"}}]}
        result = verification._check_date_format(cv)
        assert result.passed is True

    def test_consistent_month_year(self):
        cv = {"experience": [{"date_range": {"start": "Jan 2020", "end": "Present"}}]}
        result = verification._check_date_format(cv)
        assert result.passed is True

    def test_too_many_formats(self):
        cv = {
            "experience": [
                {"date_range": {"start": "2020–2024", "end": None}},
                {"date_range": {"start": "Jan 2020", "end": None}},
                {"date_range": {"start": "2020-03", "end": None}},
                {"date_range": {"start": "03/2020", "end": None}},
            ]
        }
        result = verification._check_date_format(cv)
        assert result.passed is False
        assert "inconsistent" in result.details.lower() or "different" in result.details.lower()

    def test_no_dates(self):
        cv = {"experience": [{"title": "Engineer", "date_range": {}}]}
        result = verification._check_date_format(cv)
        assert result.passed is False

    def test_empty_cv(self):
        result = verification._check_date_format({})
        assert result.passed is False


class TestCheckNoPlaceholders:
    def test_no_placeholders(self):
        cv = {
            "first_name": "Jane",
            "experience": [{"bullets": ["Built things."]}],
            "cover_letter": {"opening_paragraph": "Dear Hiring Team,"},
        }
        result = verification._check_no_placeholders(cv)
        assert result.passed is True

    def test_has_placeholder_in_cv(self):
        cv = {"first_name": "[YOUR_NAME]", "last_name": "Doe"}
        result = verification._check_no_placeholders(cv)
        assert result.passed is False
        assert "placeholder" in result.details.lower()

    def test_has_placeholder_in_cover(self):
        cv = {"cover_letter": {"opening_paragraph": "Dear [Hiring Manager"}}
        result = verification._check_no_placeholders(cv)
        assert result.passed is False

    def test_empty_cv(self):
        result = verification._check_no_placeholders({})
        assert result.passed is False

    def test_known_tokens(self):
        """All known placeholder patterns are detected."""
        cv = {
            "profile_statement": "[YOUR_NAME] [YOUR_EMAIL] [First] [Last] [COMPANY] [PHONE]",
            "experience": [{"bullets": ["++XX"]}],
        }
        result = verification._check_no_placeholders(cv)
        assert result.passed is False
        assert "placeholder" in result.details.lower()


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS: ATS-based checks
# ═══════════════════════════════════════════════════════════════════


class TestCheckCIDMarkers:
    def test_no_cid(self):
        ats = ATSResult(has_cid_markers=False, pass_ats=True)
        result = verification._check_cid_markers(ats)
        assert result.passed is True

    def test_has_cid(self):
        ats = ATSResult(has_cid_markers=True, pass_ats=False)
        result = verification._check_cid_markers(ats)
        assert result.passed is False

    def test_none_ats(self):
        result = verification._check_cid_markers(None)
        assert result.passed is True  # Skipped gracefully


class TestCheckATSContact:
    def test_all_found(self):
        ats = ATSResult(has_email=True, has_phone=True, has_candidate_name=True)
        result = verification._check_ats_contact(ats)
        assert result.passed is True

    def test_email_missing(self):
        ats = ATSResult(has_email=False, has_phone=True, has_candidate_name=True)
        result = verification._check_ats_contact(ats)
        assert result.passed is False
        assert "Email" in result.details

    def test_phone_missing(self):
        ats = ATSResult(has_email=True, has_phone=False, has_candidate_name=True)
        result = verification._check_ats_contact(ats)
        assert result.passed is False

    def test_none_ats(self):
        result = verification._check_ats_contact(None)
        assert result.passed is True  # Skipped gracefully


class TestCheckKeywordCoverage:
    def test_high_coverage(self):
        ats = ATSResult(keyword_coverage=0.85, found_keywords=["Python"], missing_keywords=[])
        result = verification._check_keyword_coverage(ats)
        assert result.passed is True

    def test_low_coverage(self):
        ats = ATSResult(keyword_coverage=0.3, found_keywords=[], missing_keywords=["Python", "PyTorch"])
        result = verification._check_keyword_coverage(ats)
        assert result.passed is False

    def test_edge_pass(self):
        ats = ATSResult(keyword_coverage=0.7, found_keywords=["Python"], missing_keywords=[])
        result = verification._check_keyword_coverage(ats)
        assert result.passed is True

    def test_none_ats(self):
        result = verification._check_keyword_coverage(None)
        assert result.passed is True  # Skipped gracefully


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS: LLM content quality checks
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_llm_content_checks_pass(sample_cv_json, sample_candidate, sample_job):
    """LLM content checks pass when no issues detected."""
    with patch("app.services.verification.llm_completion_structured") as mock_llm:
        mock_llm.return_value = LlmContentCheckOutput(
            overall_assessment="pass",
            fabricated_claims=[],
            profile_specific=True,
            tone_consistent=True,
            issues=[],
            recommendations=["Consider adding more metrics to bullets."],
        )

        checks = await verification._run_llm_content_checks(
            cv_json=sample_cv_json,
            job_posting=sample_job,
            candidate=sample_candidate,
        )

    assert len(checks) == 3
    assert all(c.passed for c in checks)
    assert any(c.name == "fabricated_claims_free" for c in checks)
    assert any(c.name == "profile_specific_to_role" for c in checks)
    assert any(c.name == "tone_consistency" for c in checks)


@pytest.mark.asyncio
async def test_llm_content_checks_fabrications(sample_candidate, sample_job):
    """LLM content checks flag fabricated claims."""
    with patch("app.services.verification.llm_completion_structured") as mock_llm:
        mock_llm.return_value = LlmContentCheckOutput(
            overall_assessment="fail",
            fabricated_claims=["Claimed 'Led a team of 10' but profile shows no leadership experience."],
            profile_specific=True,
            tone_consistent=True,
            issues=["Fabricated leadership claim."],
            recommendations=["Remove unsubstantiated leadership claims."],
        )

        checks = await verification._run_llm_content_checks(
            cv_json={"profile_statement": "Led a team of 10 engineers."},
            job_posting=sample_job,
            candidate=sample_candidate,
        )

    assert len(checks) == 3
    assert checks[0].passed is False  # fabricated_claims_free
    assert checks[0].name == "fabricated_claims_free"
    assert "fabrication" in checks[0].details.lower()


@pytest.mark.asyncio
async def test_llm_content_checks_llm_failure(sample_cv_json, sample_candidate, sample_job):
    """When LLM fails, checks are skipped gracefully (pass with warning)."""
    with patch("app.services.verification.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = Exception("LLM unavailable")

        checks = await verification._run_llm_content_checks(
            cv_json=sample_cv_json,
            job_posting=sample_job,
            candidate=sample_candidate,
        )

    assert len(checks) == 3
    assert all(c.passed for c in checks)  # Graceful pass
    assert all("not available" in c.details for c in checks)


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TEST: Full verification checklist
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_verification_checklist_full_pass(
    sample_application, sample_cv_json, sample_candidate, sample_job, sample_ats_pass
):
    """Full verification with all checks passing."""
    with patch("app.services.verification.ats_check.check_ats_from_json") as mock_ats:
        mock_ats.return_value = sample_ats_pass

        with patch("app.services.verification._run_llm_content_checks") as mock_llm:
            mock_llm.return_value = [
                VerificationCheck(
                    name="fabricated_claims_free",
                    label="No fabricated claims",
                    category="llm",
                    passed=True,
                    details="✅ No fabricated claims detected.",
                ),
                VerificationCheck(
                    name="profile_specific_to_role",
                    label="Profile statement specific to role",
                    category="llm",
                    passed=True,
                    details="✅ Profile statement mentions the specific role.",
                ),
                VerificationCheck(
                    name="tone_consistency",
                    label="Consistent tone CV/cover",
                    category="llm",
                    passed=True,
                    details="✅ CV and cover letter have consistent tone.",
                ),
            ]

            result = await verification.run_verification_checklist(
                application=sample_application,
                candidate=sample_candidate,
                job_posting=sample_job,
                cv_json=sample_cv_json,
                cv_pdf_path=Path("/tmp/test_cv.pdf"),
            )

    assert isinstance(result, VerificationResult)
    assert len(result.checks) > 0
    assert result.overall_pass is True
    assert len(result.failures) == 0
    assert len(result.passes) >= 9
    assert result.application_id == "app-1"


@pytest.mark.asyncio
async def test_run_verification_checklist_with_failures(sample_application, sample_candidate, sample_job):
    """Verification with some failing checks."""
    bad_cv = {
        "first_name": "[YOUR_NAME]",
        "last_name": "Doe",
        "email": "wrong@example.com",
        "profile_statement": "Generic professional summary.",
        "experience": [{"title": "Engineer", "date_range": {"start": "2020-01", "end": "2024-12"}}],
        "cover_letter": {"opening_paragraph": "Dear Hiring Manager,"},
    }

    bad_ats = ATSResult(
        raw_text="(cid:123) garbage text",
        has_cid_markers=True,
        has_email=False,
        has_phone=False,
        has_candidate_name=False,
        keyword_coverage=0.1,
        found_keywords=[],
        missing_keywords=["Python", "PyTorch"],
        reading_order_ok=False,
        pass_ats=False,
    )

    with patch("app.services.verification.ats_check.check_ats_from_json") as mock_ats:
        mock_ats.return_value = bad_ats

        with (
            patch("pathlib.Path.exists", return_value=True),  # ATS requires existing PDF
            patch("app.services.verification._run_llm_content_checks") as mock_llm,
        ):
            mock_llm.return_value = [
                VerificationCheck(
                    name="fabricated_claims_free",
                    label="No fabricated claims",
                    category="llm",
                    passed=True,
                    details="✅ No fabricated claims detected.",
                ),
                VerificationCheck(
                    name="profile_specific_to_role",
                    label="Profile statement specific to role",
                    category="llm",
                    passed=True,
                    details="✅ Profile statement mentions the specific role.",
                ),
                VerificationCheck(
                    name="tone_consistency",
                    label="Consistent tone CV/cover",
                    category="llm",
                    passed=True,
                    details="✅ CV and cover letter have consistent tone.",
                ),
            ]

            result = await verification.run_verification_checklist(
                application=sample_application,
                candidate=sample_candidate,
                job_posting=sample_job,
                cv_json=bad_cv,
                cv_pdf_path=Path("/tmp/test_cv.pdf"),
            )

    assert result.overall_pass is False
    assert len(result.failures) > 0
    assert "no_placeholders" in result.failures or "name_in_cv" in result.failures
    assert "no_cid_markers" in result.failures
    assert "keyword_coverage" in result.failures


@pytest.mark.asyncio
async def test_run_verification_checklist_no_pdf(sample_application, sample_cv_json, sample_candidate, sample_job):
    """Verification runs gracefully — ATS now uses JSON data, not PDF."""
    sample_ats_pass = ATSResult(
        raw_text="test text",
        has_cid_markers=False,
        has_email=True,
        has_phone=True,
        has_candidate_name=True,
        keyword_coverage=0.9,
        found_keywords=["Python"],
        missing_keywords=[],
        reading_order_ok=True,
        pass_ats=True,
    )

    with patch("app.services.verification.ats_check.check_ats_from_json") as mock_ats:
        mock_ats.return_value = sample_ats_pass
        with patch("app.services.verification._run_llm_content_checks") as mock_llm:
            mock_llm.return_value = [
                VerificationCheck(
                    name="fabricated_claims_free",
                    label="No fabricated claims",
                    category="llm",
                    passed=True,
                    details="Skipped.",
                ),
                VerificationCheck(
                    name="profile_specific_to_role",
                    label="Profile statement specific to role",
                    category="llm",
                    passed=True,
                    details="Skipped.",
                ),
                VerificationCheck(
                    name="tone_consistency",
                    label="Consistent tone CV/cover",
                    category="llm",
                    passed=True,
                    details="Skipped.",
                ),
            ]

            result = await verification.run_verification_checklist(
                application=sample_application,
                candidate=sample_candidate,
                job_posting=sample_job,
                cv_json=sample_cv_json,
                cv_pdf_path=None,
            )

    assert len(result.checks) >= 9
    assert result.overall_pass is True


@pytest.mark.asyncio
async def test_run_verification_checklist_no_candidate(sample_application, sample_cv_json, sample_job):
    """Verification runs without candidate profile (some checks skipped)."""
    with patch("app.services.verification._run_llm_content_checks") as mock_llm:
        mock_llm.return_value = [
            VerificationCheck(
                name="fabricated_claims_free",
                label="No fabricated claims",
                category="llm",
                passed=True,
                details="Skipped.",
            ),
            VerificationCheck(
                name="profile_specific_to_role",
                label="Profile statement specific to role",
                category="llm",
                passed=True,
                details="Skipped.",
            ),
            VerificationCheck(
                name="tone_consistency",
                label="Consistent tone CV/cover",
                category="llm",
                passed=True,
                details="Skipped.",
            ),
        ]

        result = await verification.run_verification_checklist(
            application=sample_application,
            candidate=None,
            job_posting=sample_job,
            cv_json=sample_cv_json,
            cv_pdf_path=None,
        )

    assert len(result.checks) >= 9
    assert result.application_id == "app-1"


# ═══════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════


def test_check_date_format_present_accepted():
    """'Present' is accepted as a valid date token."""
    cv = {"experience": [{"date_range": {"start": "2020-01", "end": "Present"}}]}
    result = verification._check_date_format(cv)
    assert result.passed is True


def test_check_keyword_coverage_edge_at_threshold():
    """Exactly 70% coverage should pass."""
    ats = ATSResult(keyword_coverage=0.7, found_keywords=["Python"], missing_keywords=[])
    result = verification._check_keyword_coverage(ats)
    assert result.passed is True


def test_check_email_in_cv_with_special_chars():
    """Email with + sign is found correctly."""
    result = verification._check_email_in_cv({"email": "jane+work@example.com"}, "jane+work@example.com")
    assert result.passed is True


def test_check_company_in_cover_empty_string():
    """Empty company name is skipped gracefully."""
    result = verification._check_company_in_cover("Some cover letter.", "")
    assert result.passed is True
