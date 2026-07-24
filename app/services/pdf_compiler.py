"""PDF compilation verification loop — compiles LaTeX with page-break verification.

Implements the FASE 9 compilation loop from the MadsLorentzen integration:
1. Compile CV (lualatex) and cover letter (xelatex)
2. Verify page counts, orphaned entries, and signature presence
3. Apply automatic fixes (\\needspace{}, \\enlargethispage{}) and recompile
4. Loop up to MAX_COMPILE_ITERATIONS (5) until all checks pass

100% DETERMINISTIC — no LLM calls in the verification loop itself.

Design rationale:
- Page count verification is already handled by ``compile_latex()``.
- This service adds ORPHAN DETECTION and AUTO-FIX on top of that.
- Orphaned ``\\cventry`` entries (titles at page bottom with content on
  next page) are a common LaTeX CV issue that ATS systems misinterpret
  as separate roles.
- The loop also verifies cover letter signature renders as text (not image).
"""

from __future__ import annotations


import re
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from app.schemas.pdf_compiler import (
    CompilationIssue,
    CompileIteration,
    CompileResult,
    IssueCategory,
    IssueSeverity,
)

from app.core.logging import get_logger, bind_context
logger = get_logger(__name__)

# Maximum iterations for the compilation verification loop
MAX_COMPILE_ITERATIONS = 5

# ── Text extraction from PDF ────────────────────────────────────────


async def _extract_pdf_page_text(pdf_path: Path) -> list[str]:
    """Extract text per page from a compiled PDF.

    Uses pdftotext -layout and splits on form-feed characters (``\\f``)
    which mark page boundaries. Each element in the returned list is the
    text content of one page.

    Args:
        pdf_path: Path to the compiled PDF.

    Returns:
        List of strings, one per page. Empty list if extraction fails.
    """
    from app.services.ats_check import _extract_pdf_text as _extract

    raw = await _extract(pdf_path)
    if raw is None:
        return []
    return raw.split("\f")


# ── Orphan detection ────────────────────────────────────────────────


def _detect_orphaned_entries(page_texts: list[str]) -> list[dict]:
    """Detect ``\\cventry`` titles that are orphaned at the bottom of a page.

    Heuristic: if a page's last 3 lines contain a date-range pattern
    (e.g. ``2020--2024``) typical of a ``\\cventry`` title line, that
    entry title is orphaned (its content bullets are on the next page).

    Args:
        page_texts: List of text per page from pdftotext output.

    Returns:
        List of dicts with ``page`` (0-indexed) and ``line_text`` for
        each orphaned entry found.
    """
    orphans: list[dict] = []
    # Date range pattern: e.g. "2020--2024", "2018--Present"
    date_range_pattern = re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
        r"\d{4})\s*\d{0,4}\s*[-\\u2013\\u2014]+\s*"
        r"(?:\d{4}|Present|Current|Now)\b"
    )

    for page_idx, text in enumerate(page_texts):
        lines = text.strip().split("\n")
        if len(lines) < 3:
            continue

        # Check the last 3 non-empty lines
        last_lines = [l.strip() for l in lines[-6:] if l.strip()][-3:]
        for line in last_lines:
            if date_range_pattern.search(line):
                orphans.append({"page": page_idx, "line_text": line.strip()})
                break  # One orphan per page is enough

    return orphans


# ── Signature detection ─────────────────────────────────────────────


def _detect_missing_signature(
    cover_page_texts: list[str],
    candidate_name: str | None,
) -> bool:
    """Check if the cover letter signature is present as rendered text.

    The signature (candidate's name) should appear in the last page's
    final lines. If pdftotext cannot extract it, the signature may be
    rendered as an image or the glyphs may be unmapped.

    Args:
        cover_page_texts: List of text per page from the cover letter PDF.
        candidate_name: The candidate's full name.

    Returns:
        True if the signature IS present (pass), False if missing (fail).
    """
    if not candidate_name or not cover_page_texts:
        return False

    # Check the last page for the name in the last 10 lines
    last_page = cover_page_texts[-1]
    lines = last_page.strip().split("\n")
    tail_lines = [l.strip() for l in lines[-10:] if l.strip()]

    name_lower = candidate_name.lower()
    for line in tail_lines:
        if name_lower in line.lower():
            return True

    return False


# ── Fix application ─────────────────────────────────────────────────


def _find_orphan_cventry_in_latex(
    latex: str,
    orphan_lines: list[str],
) -> list[int]:
    """Find the line positions of orphaned ``\\cventry`` entries in LaTeX source.

    Uses the first ~40 chars of the orphan line text to locate the
    corresponding ``\\cventry`` in the LaTeX source.

    Args:
        latex: The full LaTeX document.
        orphan_lines: List of orphan text lines from PDF extraction.

    Returns:
        List of 0-based line numbers where ``\\cventry`` entries start.
    """
    latex_lines = latex.split("\n")
    positions: list[int] = []

    for orphan_text in orphan_lines:
        # Extract the most distinctive part: the job title + company
        # The orphan line looks like: "2020--2024  Senior ML Engineer  TechCorp"
        # In LaTeX it looks like: \cventry{2020--2024}{Senior ML Engineer}{TechCorp}
        parts = orphan_text.split()
        if len(parts) < 2:
            continue

        # Use the 2nd and 3rd "words" (job title start + company name)
        # as search keys in the LaTeX
        search_terms = parts[1:4]  # e.g., ["Senior", "ML", "Engineer"]
        if not search_terms:
            continue

        for line_idx, line in enumerate(latex_lines):
            if "\\cventry" in line and all(
                term.lower() in line.lower() for term in search_terms
            ):
                positions.append(line_idx)
                break

    return positions


def _apply_needspace_fixes(latex: str, orphan_positions: list[int]) -> str:
    """Add ``\\needspace{3\\baselineskip}`` before orphaned ``\\cventry`` entries.

    The ``\\needspace`` command (from the ``needspace`` package) reserves
    vertical space at the bottom of a page, preventing the entry title
    from being orphaned. The macro ensures at least 3 lines will fit
    before a page break occurs.

    If ``needspace`` is not already loaded, this function does NOT add
    the package — it assumes it is already in the document preamble.
    (The CV template should include it; if not, a warning is logged.)

    Args:
        latex: The LaTeX document source.
        orphan_positions: 0-based line numbers of orphaned ``\\cventry``.

    Returns:
        Updated LaTeX with ``\\needspace`` hints inserted.
    """
    if not orphan_positions:
        return latex

    # Check if needspace package is loaded
    if "\\usepackage{needspace}" not in latex:
        logger.warning(
            "\\needspace package not loaded in template — "
            "orphan fixes may not work. Add \\usepackage{needspace} to the CV template."
        )

    lines = latex.split("\n")
    # Sort positions in reverse order so we insert bottom-up and
    # don't invalidate earlier positions
    sorted_positions = sorted(set(orphan_positions), reverse=True)

    for pos in sorted_positions:
        if pos < len(lines):
            # Insert \\needspace before the \\cventry line
            needspace_line = "\\needspace{3\\baselineskip}% Prevent orphaned entry"
            lines.insert(pos, needspace_line)
            logger.info(f"Added \\needspace before \\cventry at line {pos + 1}")

    return "\n".join(lines)


def _apply_enlargethispage_fix(latex: str, target: str = "cv") -> str:
    """Add ``\\enlargethispage{-\\baselineskip}`` to shrink a page slightly.

    Used as a last-resort fix when a page is just 1-2 lines over the limit.
    ``\\enlargethispage`` changes the page height for the current page only.

    Args:
        latex: The LaTeX document source.
        target: ``"cv"`` or ``"cover_letter"`` — affects where the fix is placed.

    Returns:
        Updated LaTeX with ``\\enlargethispage`` added after ``\\makecvtitle``
        or at the document start for cover letters.
    """
    if target == "cv":
        # Place after \\makecvtitle
        latex = latex.replace(
            "\\makecvtitle",
            "\\makecvtitle\n\\enlargethispage{-\\baselineskip}% Shrink to fit",
        )
    else:
        # Place after \\begin{document}
        latex = latex.replace(
            "\\begin{document}",
            "\\begin{document}\n\\enlargethispage{-\\baselineskip}% Shrink to fit",
        )

    logger.info(f"Applied \\enlargethispage fix to {target}")
    return latex


# ── Main compilation loop ───────────────────────────────────────────


async def compile_with_verification(
        cv_latex: str,
        cover_letter_latex: str,
        cv_name: str,
        cover_name: str,
        candidate_name: str | None,
        output_dir: Path,
        compile_cv_fn: Callable[[str, Path, str], Awaitable[tuple[Path, int]]],
        compile_cover_fn: Callable[[str, Path, str], Awaitable[tuple[Path, int]]],
        ats_check_fn: Callable[[Path], Awaitable[object]] | None = None,
) -> CompileResult:
    """Compile CV and cover letter with iterative page-break verification.

    The loop runs as follows:
    1. Compile CV (lualatex) and cover letter (xelatex)
    2. Check page counts against expectations (CV=2, Cover=1)
    3. Detect orphaned ``\\cventry`` entries in CV PDF text
    4. Detect missing signature in cover letter PDF text
    5. If issues found:
       a. Apply ``\\needspace{}`` before orphaned entries
       b. Apply ``\\enlargethispage{}`` for borderline page overflows
       c. Log the fixes applied
       d. Recompile and re-check (up to MAX_COMPILE_ITERATIONS)
    6. If all checks pass, run optional ATS check and return success

    Args:
        cv_latex: The CV LaTeX source.
        cover_letter_latex: The cover letter LaTeX source.
        cv_name: Base name for CV output (e.g. ``"cv_Company_Role"``).
        cover_name: Base name for cover letter output.
        candidate_name: Candidate's full name (for signature check).
        output_dir: Directory for compilation artifacts.
        compile_cv_fn: Async function for CV compilation. Must return
            ``(pdf_path, page_count)`` tuple. Should NOT raise on wrong
            page count — instead always return the actual count. Use
            ``compile_latex_get_pages`` to wrap the standard compiler.
        compile_cover_fn: Same as ``compile_cv_fn`` but for cover letter.
        ats_check_fn: Optional async function for ATS check. If provided,
            run once after all other checks pass. Must accept a PDF path
            and return a dict (not blocking on failure).

    Returns:
        ``CompileResult`` with all iterations, issues, and final paths.
    """
    with bind_context(pipeline_stage="latex"):
        current_cv = cv_latex
        current_cover = cover_letter_latex
        enlargethispage_applied = {"cv": False, "cover": False}
        final_cv_pdf: Path | None = None
        final_cover_pdf: Path | None = None
        iterations: list[CompileIteration] = []

        for iteration in range(MAX_COMPILE_ITERATIONS):
            iter_record = CompileIteration(
                iteration=iteration,
                cv_latex=current_cv,
                cover_latex=current_cover,
            )
            issues: list[CompilationIssue] = []
            fixes_applied: list[str] = []

            # ── Step 1: Compile CV ──────────────────────────────────────
            cv_pdf: Path | None = None
            cv_pages: int | None = None
            try:
                cv_pdf, cv_pages = await compile_cv_fn(current_cv, output_dir, cv_name)
                iter_record.cv_pages = cv_pages
                final_cv_pdf = cv_pdf
            except Exception as e:
                issues.append(
                    CompilationIssue(
                        category=IssueCategory.COMPILE_ERROR,
                        severity=IssueSeverity.ERROR,
                        document="cv",
                        description=f"CV compilation failed: {e}",
                    )
                )

            # ── Step 2: Compile cover letter ────────────────────────────
            cover_pdf: Path | None = None
            cover_pages: int | None = None
            try:
                cover_pdf, cover_pages = await compile_cover_fn(
                    current_cover, output_dir, cover_name
                )
                iter_record.cover_pages = cover_pages
                final_cover_pdf = cover_pdf
            except Exception as e:
                issues.append(
                    CompilationIssue(
                        category=IssueCategory.COMPILE_ERROR,
                        severity=IssueSeverity.ERROR,
                        document="cover_letter",
                        description=f"Cover letter compilation failed: {e}",
                    )
                )

            # ── Step 3: Extract text for verification ───────────────────
            cv_page_texts: list[str] = []
            cover_page_texts: list[str] = []
            if cv_pdf and cv_pdf.exists():
                cv_page_texts = await _extract_pdf_page_text(cv_pdf)
            if cover_pdf and cover_pdf.exists():
                cover_page_texts = await _extract_pdf_page_text(cover_pdf)

            # ── Step 4: Check CV page count ─────────────────────────────
            if cv_pages is not None and cv_pages != 2:
                severity = IssueSeverity.WARNING if cv_pages <= 2 else IssueSeverity.ERROR
                issues.append(
                    CompilationIssue(
                        category=IssueCategory.PAGE_COUNT,
                        severity=severity,
                        document="cv",
                        description=f"CV has {cv_pages} page(s), expected 2.",
                        line_ref=f"actual={cv_pages}, expected=2",
                    )
                )

            # ── Step 5: Check cover letter page count ───────────────────
            if cover_pages is not None and cover_pages != 1:
                severity = IssueSeverity.ERROR if cover_pages > 1 else IssueSeverity.WARNING
                issues.append(
                    CompilationIssue(
                        category=IssueCategory.PAGE_COUNT,
                        severity=severity,
                        document="cover_letter",
                        description=f"Cover letter has {cover_pages} page(s), expected 1.",
                        line_ref=f"actual={cover_pages}, expected=1",
                    )
                )

            # ── Step 6: Detect orphaned entries ─────────────────────────
            orphans = _detect_orphaned_entries(cv_page_texts)
            orphan_line_texts = [o["line_text"] for o in orphans]
            if orphans:
                for orphan in orphans:
                    issues.append(
                        CompilationIssue(
                            category=IssueCategory.ORPHANED_ENTRY,
                            severity=IssueSeverity.WARNING,
                            document="cv",
                            description=(
                                f"Orphaned \\\\cventry on page {orphan['page'] + 1}: "
                                f"\"{orphan['line_text'][:60]}\""
                            ),
                            line_ref=orphan["line_text"],
                        )
                    )

            # ── Step 7: Detect missing signature ────────────────────────
            if not _detect_missing_signature(cover_page_texts, candidate_name):
                issues.append(
                    CompilationIssue(
                        category=IssueCategory.MISSING_SIGNATURE,
                        severity=IssueSeverity.WARNING,
                        document="cover_letter",
                        description=(
                            f"Candidate name \"{candidate_name}\" not found "
                            f"as extractable text in cover letter PDF."
                        ),
                    )
                )

            iter_record.issues = issues
            iterations.append(iter_record)

            # ── Step 8: If no issues, we're done! ───────────────────────
            if not issues:
                logger.info(
                    f"Compilation verified in {iteration + 1} iteration(s): "
                    f"CV={cv_pages}p, Cover={cover_pages}p, no issues."
                )
                break

            # ── Step 9: Apply automatic fixes ───────────────────────────
            # Check if we've already exhausted all fixes for this iteration
            if iteration >= MAX_COMPILE_ITERATIONS - 1:
                logger.warning(
                    f"Compilation loop exhausted ({iteration + 1} iterations). "
                    f"Remaining issues: {len(issues)}"
                )
                break

            # Determine which fixes to apply based on detected issues
            cv_orphan_lines = [
                o["line_text"] for o in orphans
            ]
            cv_orphan_positions = _find_orphan_cventry_in_latex(
                current_cv, cv_orphan_lines
            )

            if cv_orphan_positions:
                current_cv = _apply_needspace_fixes(current_cv, cv_orphan_positions)
                fixes_applied.append(
                    f"Added \\\\needspace before {len(cv_orphan_positions)} "
                    f"orphaned \\\\cventry entr(y/ies)"
                )

                # If we have both orphan issues AND page count issues, try \\enlargethispage
                if cv_pages is not None and cv_pages > 2 and not enlargethispage_applied["cv"]:
                    current_cv = _apply_enlargethispage_fix(current_cv, "cv")
                    enlargethispage_applied["cv"] = True
                    fixes_applied.append("Added \\\\enlargethispage to CV")
            elif cv_pages is not None and cv_pages > 2 and not enlargethispage_applied["cv"]:
                # Try \\enlargethispage for borderline page overflow
                current_cv = _apply_enlargethispage_fix(current_cv, "cv")
                enlargethispage_applied["cv"] = True
                fixes_applied.append("Added \\\\enlargethispage to CV")

            # Cover letter fixes
            if cover_pages is not None and cover_pages != 1 and not enlargethispage_applied["cover"]:
                current_cover = _apply_enlargethispage_fix(current_cover, "cover_letter")
                enlargethispage_applied["cover"] = True
                fixes_applied.append("Added \\\\enlargethispage to cover letter")

            iter_record.fixes_applied = fixes_applied
            logger.info(
                f"Iteration {iteration + 1}: {len(issues)} issue(s), "
                f"applied {len(fixes_applied)} fix(es). Recompiling..."
            )

        # ── Step 10: Build final result ─────────────────────────────────
        success = all(
            i.severity != IssueSeverity.ERROR
            for iter_rec in iterations
            for i in iter_rec.issues
        )

        # Run ATS check on final CV if provided
        if ats_check_fn and final_cv_pdf and final_cv_pdf.exists():
            try:
                ats_result = await ats_check_fn(final_cv_pdf)
                # ATS check failed — add as a warning, not blocking
                # model_dump() is the canonical Pydantic v2 API for BaseModel serialization
                ats_dict = ats_result if isinstance(ats_result, dict) else ats_result.model_dump()
                if ats_dict.get("pass_ats", False) is False:
                    if ats_dict.get("has_cid_markers"):
                        issues.append(
                            CompilationIssue(
                                category=IssueCategory.CID_MARKERS,
                                severity=IssueSeverity.WARNING,
                                document="cv",
                                description=(
                                    "CID (cid:*) glyph markers found in PDF — "
                                    "ATS systems may not extract text correctly."
                                ),
                            )
                        )
            except Exception as e:
                logger.warning(f"ATS check in compilation loop failed (non-blocking): {e}")

        # Gather final unresolved issues (warnings only at this point)
        all_issues: list[CompilationIssue] = []
        for iter_rec in iterations:
            for issue in iter_rec.issues:
                if issue not in all_issues:
                    all_issues.append(issue)

        return CompileResult(
            success=success,
            cv_pdf_path=str(final_cv_pdf) if final_cv_pdf else None,
            cover_pdf_path=str(final_cover_pdf) if final_cover_pdf else None,
            cv_pages=iter_record.cv_pages,
            cover_pages=iter_record.cover_pages,
            iterations=iterations,
            total_iterations=len(iterations),
            final_issues=[i for i in all_issues if i.severity != IssueSeverity.ERROR],
            cv_latex=current_cv,
            cover_latex=current_cover,
        )
