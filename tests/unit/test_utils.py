"""Tests for utility tools (FASE 12).

Tests cover:
- app/utils/pdf_verifier.py  (wraps existing ats_check)
- app/utils/skill_linter.py   (skill validation)
- app/middleware/content_guard.py (content security)
"""

import pytest
from pathlib import Path

from app.middleware.content_guard import (
    check_placeholders,
    check_sensitive_data,
    guard_content,
    guard_latex,
    guard_text,
)
from app.utils.skill_linter import (
    KNOWN_SKILLS,
    add_known_skills,
    lint_skill,
    lint_skills_list,
    lint_skills_dict,
    _resolve_alias,
    _find_closest_known_skill,
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
# SKILL LINTER
# ═══════════════════════════════════════════════════════════════════


class TestSkillLinter:

    def test_lint_known_good_skill(self):
        """A well-known skill passes with no errors."""
        result = lint_skill("Python")
        assert result.passed
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_lint_vague_skill(self):
        """Vague skills produce warnings."""
        result = lint_skill("coding")
        assert result.passed  # Warnings don't fail
        assert len(result.warnings) > 0
        assert "vague" in result.warnings[0].lower()

    def test_lint_empty_skill(self):
        """Empty skill produces an error."""
        result = lint_skill("")
        assert not result.passed
        assert len(result.errors) > 0

    def test_lint_very_long_skill(self):
        """Very long skill produces an error."""
        result = lint_skill("A" * 101)
        assert not result.passed
        assert any("too long" in e.lower() for e in result.errors)

    def test_lint_skill_too_short(self):
        """Single character skill produces an error."""
        result = lint_skill("X")
        assert not result.passed

    def test_resolve_alias_exact_match(self):
        """_resolve_alias returns canonical name for known aliases."""
        assert _resolve_alias("pytorch") == "PyTorch"
        assert _resolve_alias("sklearn") == "scikit-learn"
        assert _resolve_alias("k8s") == "Kubernetes"

    def test_resolve_alias_nonexistent(self):
        """_resolve_alias returns None for unknown skills."""
        assert _resolve_alias("flibberflabber") is None

    def test_find_closest_known_skill_substring(self):
        """_find_closest_known_skill finds match by substring."""
        match = _find_closest_known_skill("pytorch")
        assert match == "PyTorch"

    def test_lint_skills_list_multiple(self):
        """lint_skills_list processes multiple skills."""
        result = lint_skills_list(["Python", "coding", "Kubernetes"])
        assert result.passed  # Warnings don't fail
        assert len(result.warnings) > 0  # "coding" is vague

    def test_lint_skills_dict_programming_ml(self):
        """lint_skills_dict handles programming_ml section."""
        skills_dict = {
            "programming_ml": [
                {"language": "Python", "proficiency": "Expert", "frameworks": ["PyTorch"]},
            ],
            "domain_expertise": ["Machine Learning"],
            "software_tools": ["Docker", "Kubernetes"],
        }
        result = lint_skills_dict(skills_dict)
        assert result.passed

    def test_lint_skills_dict_vague_skills(self):
        """lint_skills_dict flags vague skills."""
        skills_dict = {
            "programming_ml": [],
            "domain_expertise": ["computers"],
            "software_tools": ["coding"],
        }
        result = lint_skills_dict(skills_dict)
        assert result.passed  # Warnings don't fail
        assert len(result.warnings) > 0

    def test_lint_skills_dict_empty(self):
        """lint_skills_dict handles empty skills dict."""
        result = lint_skills_dict({})
        assert result.passed

    def test_add_known_skills(self):
        """add_known_skills extends the known skills dictionary."""
        add_known_skills({"CustomSkill": {"aliases": ["custom"], "category": "custom"}})
        assert "CustomSkill" in KNOWN_SKILLS
        assert _resolve_alias("custom") == "CustomSkill"

    def test_lint_skill_with_suggestion(self):
        """lint_skill suggests canonical name for alias."""
        # "pytorch" is an alias, should suggest "PyTorch"
        result = lint_skill("pytorch")
        assert len(result.suggestions) > 0
        assert any(s[1] == "PyTorch" for s in result.suggestions)


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
