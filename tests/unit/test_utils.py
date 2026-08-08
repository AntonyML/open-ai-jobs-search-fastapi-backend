"""Tests for utility tools.

Tests cover:
- app/utils/pdf_verifier.py  (wraps existing ats_check)
- app/middleware/content_guard.py (content security)
"""

from app.middleware.content_guard import (
    check_placeholders,
    check_sensitive_data,
    guard_content,
    guard_latex,
    guard_text,
)
from app.utils.pdf_verifier import verify_pdf_sync


# ═══════════════════════════════════════════════════════════════════
# PDF VERIFIER
# ═══════════════════════════════════════════════════════════════════


class TestPdfVerifier:

    def test_verify_pdf_sync_imports_correctly(self):
        """verify_pdf_sync is a function that can be imported."""
        import asyncio
        assert callable(verify_pdf_sync)

    def test_pdf_verifier_module_has_verify_pdf(self):
        """Module exports verify_pdf and verify_pdf_sync."""
        from app.utils.pdf_verifier import verify_pdf
        assert callable(verify_pdf)


# ═══════════════════════════════════════════════════════════════════
# CONTENT GUARD
# ═══════════════════════════════════════════════════════════════════


class TestContentGuard:

    def test_clean_content_passes(self):
        """Clean content with no issues passes."""
        result = guard_content("This is a clean CV with no issues.")
        assert result.passed
        assert len(result.issues) == 0

    def test_sensitive_data_ssn(self):
        """SSN pattern is detected."""
        result = guard_content("My SSN is 123-45-6789")
        assert not result.passed
        assert "ssn" in result.sensitive_data_found

    def test_sensitive_data_credit_card(self):
        """Credit card pattern is detected."""
        result = guard_content("Card: 4111-1111-1111-1111")
        assert not result.passed
        assert "credit_card" in result.sensitive_data_found

    def test_placeholder_name_detected(self):
        """[YOUR_NAME] placeholder is detected."""
        result = guard_content("Dear [YOUR_NAME]")
        assert not result.passed
        assert "name_placeholder" in result.placeholders_found

    def test_placeholder_company_detected(self):
        """[COMPANY_NAME] placeholder is detected."""
        result = guard_content("Apply to [COMPANY_NAME]")
        assert not result.passed
        assert "company_placeholder" in result.placeholders_found

    def test_placeholder_role_detected(self):
        """[JOB_TITLE] placeholder is detected."""
        result = guard_content("For the role of [JOB_TITLE]")
        assert not result.passed
        assert "role_placeholder" in result.placeholders_found

    def test_placeholder_generic_detected(self):
        """Generic [PLACEHOLDER] patterns are detected."""
        result = guard_content("Some [UNKNOWN_PLACEHOLDER] here")
        assert not result.passed
        assert len(result.placeholders_found) > 0

    def test_guard_latex(self):
        """guard_latex checks LaTeX content."""
        result = guard_latex(r"\section{Experience}\nWorked at Company")
        assert result.passed

    def test_guard_latex_with_placeholder(self):
        """guard_latex detects placeholders in LaTeX."""
        result = guard_latex(r"\name{[YOUR_NAME]}")
        assert not result.passed
        assert "name_placeholder" in result.placeholders_found

    def test_guard_latex_cv_allows_contact(self):
        """guard_latex with is_cv=True allows contact info (CV header)."""
        result = guard_latex(r"\email{john@example.com}", is_cv=True)
        # CVs allow contact info by design — no sensitive data errors
        assert len(result.sensitive_data_found) == 0
        assert len(result.placeholders_found) == 0

    def test_guard_text(self):
        """guard_text checks plain text content."""
        result = guard_text("Dear Hiring Manager", content_type="cover_letter")
        assert result.passed

    def test_check_placeholders_empty(self):
        """check_placeholders returns empty for clean content."""
        assert check_placeholders("No placeholders here") == []

    def test_check_placeholders_found(self):
        """check_placeholders detects multiple placeholders."""
        found = check_placeholders("[YOUR_NAME] at [COMPANY_NAME]")
        assert len(found) >= 2

    def test_check_sensitive_data_clean(self):
        """check_sensitive_data returns empty for clean content."""
        assert check_sensitive_data("Clean content") == []

    def test_check_sensitive_data_found(self):
        """check_sensitive_data detects sensitive patterns."""
        found = check_sensitive_data("SSN: 123-45-6789, Card: 4111-1111-1111-1111")
        assert len(found) >= 2

    def test_summary_passed(self):
        """Summary says passed when no issues."""
        result = guard_content("Clean content")
        assert "passed" in result.summary.lower()

    def test_summary_with_issues(self):
        """Summary lists issue counts."""
        result = guard_content("[YOUR_NAME] at 123-45-6789")
        assert not result.passed
        assert "placeholder" in result.summary.lower()
