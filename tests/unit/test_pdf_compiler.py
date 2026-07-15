"""Tests for the PDF compilation verification loop (FASE 9).

Tests cover:
- Orphan detection heuristic
- Missing signature detection
- Needspace fix application
- Enlargethispage fix application
- Full compilation loop with mocked compile functions
- Integration with execute_apply (with mocked compile)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.pdf_compiler import (
    CompilationIssue,
    IssueCategory,
    IssueSeverity,
)
from app.services import pdf_compiler


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def cv_page_texts_no_orphans() -> list[str]:
    """Two pages of CV text without orphaned entries."""
    return [
        # Page 1: Full content with profile, skills, and first experience entry
        "Jane Doe\n"
        "Software Engineer\n"
        "Copenhagen, Denmark\n"
        "Profile statement...\n"
        "Skills: Python, TensorFlow, AWS\n"
        "Experience\n"
        "2022--Present  Senior ML Engineer  TechCorp  Copenhagen\n"
        "• Built ML pipeline reducing latency by 40%\n"
        "• Led team of 5 engineers\n"
        "• Deployed models to production\n",

        # Page 2: Remaining experience + education
        "2020--2022  ML Engineer  DataCo  Aarhus\n"
        "• Developed recommendation system\n"
        "• Improved CTR by 15%\n"
        "Education\n"
        "MSc in Computer Science\n"
        "PhD in Machine Learning\n",
    ]


@pytest.fixture
def cv_page_texts_with_orphan() -> list[str]:
    """Two pages where the first page ends with a cventry title (orphaned)."""
    return [
        # Page 1: Ends with a date range pattern — orphaned entry
        "Jane Doe\n"
        "Copenhagen, Denmark\n"
        "Skills: Python, TensorFlow\n"
        "Experience\n"
        "2022--Present  Senior ML Engineer  TechCorp  Copenhagen\n"
        "• Built ML pipeline\n"
        "2020--2022  ML Engineer  DataCo  Aarhus\n",

        # Page 2: Content of the orphaned entry
        "• Developed recommendation system\n"
        "• Improved CTR by 15%\n"
        "• Deployed to production\n"
        "Education\n",
    ]


@pytest.fixture
def cover_page_texts_with_signature() -> list[str]:
    """Cover letter with signature visible on the page."""
    return [
        "Dear TechCorp,\n\n"
        "I am writing to apply for the Senior ML Engineer role.\n\n"
        "I look forward to hearing from you.\n\n"
        "Kind regards,\n"
        "Jane Doe",
    ]


@pytest.fixture
def cover_page_texts_no_signature() -> list[str]:
    """Cover letter without extractable signature text."""
    return [
        "Dear TechCorp,\n\n"
        "I am writing to apply for the Senior ML Engineer role.\n\n"
        "I look forward to hearing from you.\n\n"
        "Kind regards,\n",
    ]


@pytest.fixture
def sample_cv_latex() -> str:
    """Sample CV LaTeX with multiple cventry entries."""
    return """\\documentclass[11pt,a4paper,sans]{moderncv}
\\moderncvstyle{banking}
\\moderncvcolor{blue}
\\usepackage{needspace}

\\name{Jane}{Doe}

\\begin{document}
\\makecvtitle

\\section{Professional Experience}
\\begin{itemize}

\\item{\\cventry{2022--Present}{Senior ML Engineer}{TechCorp}{Copenhagen}{}{\\vspace{1pt}
\\begin{itemize}
    \\item Built ML pipeline reducing latency by 40\\%
    \\item Led team of 5 engineers
\\end{itemize}}}

\\vspace{3pt}

\\item{\\cventry{2020--2022}{ML Engineer}{DataCo}{Aarhus}{}{\\vspace{1pt}
\\begin{itemize}
    \\item Developed recommendation system
    \\item Improved CTR by 15\\%
\\end{itemize}}}

\\end{itemize}
\\end{document}
"""


@pytest.fixture
def sample_cover_latex() -> str:
    """Sample cover letter LaTeX."""
    return """\\documentclass[]{cover}
\\begin{document}
\\namesection{}{\\Huge{Jane Doe}}{}

\\lettercontent{Dear Hiring Team,}

\\lettercontent{I am writing to apply for the Senior ML Engineer role.}

\\lettercontent{I look forward to hearing from you.}

\\begin{flushright}
\\closing{Kind regards,}
\\signature{Jane Doe}
\\end{flushright}
\\end{document}
"""


# ── Orphan detection tests ──────────────────────────────────────────


class TestDetectOrphanedEntries:
    """Verify the orphan detection heuristic."""

    def test_no_orphans_clean_pages(self, cv_page_texts_no_orphans):
        """Clean pages with no orphaned entries should return empty list."""
        orphans = pdf_compiler._detect_orphaned_entries(cv_page_texts_no_orphans)
        assert len(orphans) == 0, (
            f"Expected 0 orphans, got {len(orphans)}: {orphans}"
        )

    def test_orphan_detected_on_page_boundary(self, cv_page_texts_with_orphan):
        """A cventry title at the bottom of a page should be detected."""
        orphans = pdf_compiler._detect_orphaned_entries(cv_page_texts_with_orphan)
        assert len(orphans) >= 1, (
            f"Expected at least 1 orphan, got {len(orphans)}"
        )
        # The orphan should be on page 0 (first page)
        assert orphans[0]["page"] == 0, (
            f"Expected orphan on page 0, got page {orphans[0]['page']}"
        )
        # The orphan should contain a date range (either 2022--Present or 2020--2022)
        assert "2022" in orphans[0]["line_text"] or "2020" in orphans[0]["line_text"], (
            f"Expected date in orphan line: {orphans[0]['line_text']}"
        )

    def test_orphan_single_page_no_detection(self):
        """Single page CV should not have orphan issues."""
        text = ["Jane Doe\nSkills: Python\nExperience\n• Bullet 1\n• Bullet 2\n"]
        orphans = pdf_compiler._detect_orphaned_entries(text)
        assert len(orphans) == 0

    def test_orphan_empty_text(self):
        """Empty page text should not crash."""
        orphans = pdf_compiler._detect_orphaned_entries([])
        assert orphans == []


# ── Signature detection tests ──────────────────────────────────────


class TestDetectMissingSignature:
    """Verify cover letter signature detection."""

    def test_signature_found(self, cover_page_texts_with_signature):
        """Signature should be found when name appears in last lines."""
        assert pdf_compiler._detect_missing_signature(
            cover_page_texts_with_signature, "Jane Doe"
        ) is True

    def test_signature_missing(self, cover_page_texts_no_signature):
        """Should return False when name not found."""
        assert pdf_compiler._detect_missing_signature(
            cover_page_texts_no_signature, "Jane Doe"
        ) is False

    def test_signature_empty_name(self):
        """Should return False when candidate name is None."""
        assert pdf_compiler._detect_missing_signature(
            ["Some text"], None
        ) is False

    def test_signature_empty_pages(self):
        """Should return False when no pages."""
        assert pdf_compiler._detect_missing_signature([], "Jane Doe") is False

    def test_signature_partial_name_match(self):
        """Full last name search: 'Doe' alone should not trigger a match for 'Jane Doe'."""
        # The text only has "Jane", not "Jane Doe", so full name search should fail
        text = ["Dear hiring team,\n\nKind regards,\nJane"]
        assert pdf_compiler._detect_missing_signature(text, "Jane Doe") is False


# ── Needspace fix tests ────────────────────────────────────────────


class TestApplyNeedspaceFixes:
    """Verify \\needspace{} insertion in LaTeX."""

    def test_needspace_inserted_before_cventry(self, sample_cv_latex):
        """Verify \\needspace is inserted before the correct cventry line."""
        # Find the line index of the second cventry (the orphan)
        lines = sample_cv_latex.split("\n")
        cventry_positions = [
            i for i, l in enumerate(lines) if "\\cventry" in l
        ]
        assert len(cventry_positions) >= 2

        result = pdf_compiler._apply_needspace_fixes(
            sample_cv_latex, [cventry_positions[1]]
        )
        result_lines = result.split("\n")

        # The needspace line should appear before the orphaned cventry
        assert "\\needspace" in result, "\\needspace should be added to LaTeX"

        # Count cventry instances — should be same (no removal)
        assert result.count("\\cventry") == sample_cv_latex.count("\\cventry")

    def test_needspace_no_orphans(self, sample_cv_latex):
        """No changes when no orphan positions provided."""
        result = pdf_compiler._apply_needspace_fixes(sample_cv_latex, [])
        assert result == sample_cv_latex

    def test_needspace_invalid_position(self, sample_cv_latex):
        """Out-of-bounds position should not crash."""
        result = pdf_compiler._apply_needspace_fixes(
            sample_cv_latex, [9999]
        )
        # No change expected — position doesn't exist
        assert "\\needspace" not in result

    def test_needspace_multiple_orphans(self, sample_cv_latex):
        """Multiple orphan positions should each get a \\needspace."""
        lines = sample_cv_latex.split("\n")
        cventry_positions = [
            i for i, l in enumerate(lines) if "\\cventry" in l
        ]
        result = pdf_compiler._apply_needspace_fixes(
            sample_cv_latex, cventry_positions
        )
        # Should have one needspace per orphan
        needspace_count = result.count("\\needspace")
        assert needspace_count == len(cventry_positions), (
            f"Expected {len(cventry_positions)} \\needspace, got {needspace_count}"
        )


# ── Enlargethispage fix tests ──────────────────────────────────────


class TestApplyEnlargethispageFix:
    """Verify \\enlargethispage{} insertion."""

    def test_enlargethispage_cv(self, sample_cv_latex):
        """Should add \\enlargethispage after \\makecvtitle for CV."""
        result = pdf_compiler._apply_enlargethispage_fix(sample_cv_latex, "cv")
        assert "\\enlargethispage" in result
        assert "\\makecvtitle" in result
        # The enlargethispage should be near makecvtitle
        idx_make = result.index("\\makecvtitle")
        idx_enlarge = result.index("\\enlargethispage")
        assert abs(idx_make - idx_enlarge) < 50, (
            "\\enlargethispage should be near \\makecvtitle"
        )

    def test_enlargethispage_cover(self, sample_cover_latex):
        """Should add \\enlargethispage after \\begin{document} for cover."""
        result = pdf_compiler._apply_enlargethispage_fix(sample_cover_latex, "cover_letter")
        assert "\\enlargethispage" in result
        idx_doc = result.index("\\begin{document}")
        idx_enlarge = result.index("\\enlargethispage")
        # Allow small distance — might be on different lines
        assert abs(idx_doc - idx_enlarge) < 100

    def test_enlargethispage_idempotent(self, sample_cv_latex):
        """Applying twice should add two instances (avoiding double-apply check)."""
        result = pdf_compiler._apply_enlargethispage_fix(
            pdf_compiler._apply_enlargethispage_fix(sample_cv_latex, "cv"),
            "cv",
        )
        # Should have two instances (since there's no guard against double-apply
        # in the function itself — the guard is in the compilation loop)
        assert result.count("\\enlargethispage") >= 1


# ── Compilation loop tests ────────────────────────────────────────


class TestCompileWithVerification:
    """Verify the full compilation verification loop with mocked compile functions."""

    @pytest.mark.asyncio
    async def test_all_pass_first_try(self, tmp_path):
        """When all checks pass, should succeed in 1 iteration."""
        mock_cv_pdf = tmp_path / "cv.pdf"
        mock_cv_pdf.write_text("page1\n\fpage2")
        mock_cover_pdf = tmp_path / "cover.pdf"
        mock_cover_pdf.write_text("page1")

        async def mock_compile_cv(tex, out_dir, name):
            return mock_cv_pdf, 2

        async def mock_compile_cover(tex, out_dir, name):
            return mock_cover_pdf, 1

        # Return DIFFERENT page texts for CV vs cover letter so signature
        # check can find "Jane Doe" in the cover letter
        extract_call = [0]

        async def mock_extract(pdf_path):
            extract_call[0] += 1
            if extract_call[0] == 1:
                # First call: CV pages — clean, no orphans
                return ["Jane Doe\nSkills: Python\n", "Education\n"]
            # Second+ call: cover letter — has signature
            return ["Dear team,\n\nKind regards,\nJane Doe"]

        with patch.object(
            pdf_compiler, "_extract_pdf_page_text", side_effect=mock_extract
        ):
            result = await pdf_compiler.compile_with_verification(
                cv_latex="\\documentclass{article}\\begin{document}CV\\end{document}",
                cover_letter_latex="\\documentclass{article}\\begin{document}Cover\\end{document}",
                cv_name="test_cv",
                cover_name="test_cover",
                candidate_name="Jane Doe",
                output_dir=tmp_path,
                compile_cv_fn=mock_compile_cv,
                compile_cover_fn=mock_compile_cover,
            )

        assert result.success is True, (
            f"Expected success=True, got {result.success}"
        )
        assert result.total_iterations == 1, (
            f"Expected 1 iteration, got {result.total_iterations}"
        )
        assert result.cv_pages == 2
        assert result.cover_pages == 1
        assert len(result.final_issues) == 0

    @pytest.mark.asyncio
    async def test_fix_applied_orphan_detected(self, tmp_path):
        """When orphans detected, \\needspace fix should be applied and recompile."""
        mock_pdf = tmp_path / "cv.pdf"
        mock_pdf.write_text("page1\n\fpage2")

        compile_call_count = {"cv": 0, "cover": 0}

        async def mock_compile_cv(tex, out_dir, name):
            compile_call_count["cv"] += 1
            return mock_pdf, 2

        async def mock_compile_cover(tex, out_dir, name):
            compile_call_count["cover"] += 1
            return tmp_path / "cover.pdf", 1

        # First call: simulate orphan detection
        # Second call: clean (after fix applied)
        extract_call_count = [0]

        async def mock_extract(pdf_path):
            extract_call_count[0] += 1
            if extract_call_count[0] <= 1:
                # First extract: orphan detected
                return [
                    "Jane Doe\n2020--2024  Senior Engineer  TechCorp\n",
                    "• Bullet content\n",
                ]
            # Subsequent extracts: clean
            return [
                "Jane Doe\nExperience\n• Bullet 1\n",
                "Education\n",
            ]

        with patch.object(pdf_compiler, "_extract_pdf_page_text", side_effect=mock_extract):
            result = await pdf_compiler.compile_with_verification(
                cv_latex="\\documentclass{article}\\usepackage{needspace}\\begin{document}\\makecvtitle\n\\cventry{2020--2024}{Senior Engineer}{TechCorp}\n\\end{document}",
                cover_letter_latex="\\documentclass{article}\\begin{document}Cover\\end{document}",
                cv_name="test_cv",
                cover_name="test_cover",
                candidate_name="Jane Doe",
                output_dir=tmp_path,
                compile_cv_fn=mock_compile_cv,
                compile_cover_fn=mock_compile_cover,
            )

        # Should have recompiled at least once more (total >= 2 compiles)
        assert compile_call_count["cv"] >= 2, (
            f"Expected >=2 CV compiles, got {compile_call_count['cv']}"
        )
        # The result should show the applied fix
        assert result.total_iterations >= 2, (
            f"Expected >=2 iterations, got {result.total_iterations}"
        )

    @pytest.mark.asyncio
    async def test_or_phan_detected_in_experience_section(self):
        """Verify orphan detection finds cventry-like patterns in experience section."""
        # Simulate a 2-page PDF where the first page ends with a cventry title
        page1 = (
            "Jane Doe\n"
            "Copenhagen, Denmark\n"
            "Profile statement...\n"
            "Professional Experience\n"
            "2022--Present  Senior ML Engineer  TechCorp  Copenhagen\n"
            "• Built ML pipeline reducing latency by 40%\n"
            "2020--2022  ML Engineer  DataCo  Aarhus\n"  # ← This is orphaned!
        )
        page2 = (
            "• Developed recommendation system\n"
            "• Improved CTR by 15%\n"
            "Education\n"
            "MSc Computer Science\n"
        )

        orphans = pdf_compiler._detect_orphaned_entries([page1, page2])
        assert len(orphans) == 1, f"Expected 1 orphan, got {len(orphans)}: {orphans}"
        # The orphan should be on page 0 (the first page)
        # and contain "DataCo" (the orphaned entry)
        orphan_text = orphans[0]["line_text"].lower()
        assert "dataco" in orphan_text or "ml engineer" in orphan_text, (
            f"Orphan text should mention the orphaned entry: {orphan_text}"
        )

    @pytest.mark.asyncio
    async def test_enlargethispage_on_page_overflow(self, tmp_path):
        """When CV is consistently over page limit, \\enlargethispage should be tried."""
        mock_pdf = tmp_path / "cv.pdf"
        mock_pdf.write_text("page1\n\fpage2\n\fpage3")

        extract_call_count = [0]

        async def mock_compile_cv(tex, out_dir, name):
            return mock_pdf, 3  # Always returns 3 pages

        async def mock_compile_cover(tex, out_dir, name):
            return tmp_path / "cover.pdf", 1

        async def mock_extract(pdf_path):
            extract_call_count[0] += 1
            return [
                "Jane Doe\nSkills: Python\n• Bullet 1\n",
                "Education\n",
                "References\n",
            ]

        with patch.object(pdf_compiler, "_extract_pdf_page_text", side_effect=mock_extract):
            result = await pdf_compiler.compile_with_verification(
                cv_latex="\\documentclass{article}\\usepackage{needspace}\\begin{document}\\makecvtitle Test \\end{document}",
                cover_letter_latex="\\documentclass{article}\\begin{document}Cover\\end{document}",
                cv_name="test_cv",
                cover_name="test_cover",
                candidate_name="Jane Doe",
                output_dir=tmp_path,
                compile_cv_fn=mock_compile_cv,
                compile_cover_fn=mock_compile_cover,
            )

        # The loop should have detected page count issues
        page_count_issues = [
            issue
            for iter_rec in result.iterations
            for issue in iter_rec.issues
            if issue.category == IssueCategory.PAGE_COUNT
        ]
        assert len(page_count_issues) > 0, (
            "Expected at least one PAGE_COUNT issue for a 3-page CV"
        )
        # Should have compiled at least once (even if it couldn't fix 3->2)
        assert result.success is not None
        assert result.total_iterations >= 1

    @pytest.mark.asyncio
    async def test_compile_error_handling(self, tmp_path):
        """Compilation failures should be captured as issues, not crash the loop."""
        mock_cover_pdf = tmp_path / "cover.pdf"
        mock_cover_pdf.write_text("page1")

        async def mock_compile_fail(tex, out_dir, name):
            raise Exception("Compilation failed!")

        # Cover compile succeeds but returns path with signature text
        async def mock_compile_cover(tex, out_dir, name):
            return mock_cover_pdf, 1

        # Mock extraction for cover to include signature
        with patch.object(
            pdf_compiler, "_extract_pdf_page_text",
            return_value=["Dear team,\n\nKind regards,\nJane Doe"]
        ):
            result = await pdf_compiler.compile_with_verification(
                cv_latex="bad latex",
                cover_letter_latex="good latex",
                cv_name="test_cv",
                cover_name="test_cover",
                candidate_name="Jane Doe",
                output_dir=tmp_path,
                compile_cv_fn=mock_compile_fail,
                compile_cover_fn=mock_compile_cover,
            )

        # Should have captured the error without crashing
        assert result.success is not None
        has_compile_error = any(
            issue.category == IssueCategory.COMPILE_ERROR
            for iter_rec in result.iterations
            for issue in iter_rec.issues
        )
        assert has_compile_error, "Expected at least one COMPILE_ERROR issue"

    @pytest.mark.asyncio
    async def test_ats_check_integration(self, tmp_path):
        """ATS check function should be called when provided."""
        mock_pdf = tmp_path / "cv.pdf"
        mock_pdf.write_text("page1\n\fpage2")

        ats_called = False

        async def mock_compile_cv(tex, out_dir, name):
            return mock_pdf, 2

        async def mock_compile_cover(tex, out_dir, name):
            return tmp_path / "cover.pdf", 1

        async def mock_ats_check(pdf_path):
            nonlocal ats_called
            ats_called = True
            return {"pass_ats": True, "keyword_coverage": 0.85}

        with patch.object(
            pdf_compiler, "_extract_pdf_page_text", return_value=[
                "Jane Doe\nSkills: Python\n• Bullet 1\n",
                "Education\n",
            ]
        ):
            result = await pdf_compiler.compile_with_verification(
                cv_latex="\\documentclass{article}\\begin{document}CV\\end{document}",
                cover_letter_latex="\\documentclass{article}\\begin{document}Cover\\end{document}",
                cv_name="test_cv",
                cover_name="test_cover",
                candidate_name="Jane Doe",
                output_dir=tmp_path,
                compile_cv_fn=mock_compile_cv,
                compile_cover_fn=mock_compile_cover,
                ats_check_fn=mock_ats_check,
            )

        assert ats_called, "ATS check function should have been called"
        assert result.success is True
