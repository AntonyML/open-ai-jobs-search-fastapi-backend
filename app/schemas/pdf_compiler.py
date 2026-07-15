"""PDF compiler schemas — verification and auto-fix for compiled LaTeX documents.

Captures the result of each compilation attempt, including detected issues,
fixes applied, and the final outcome after the verification loop.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IssueSeverity(str, Enum):
    """Severity of a compilation issue."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueCategory(str, Enum):
    """Category of a compilation issue."""
    PAGE_COUNT = "page_count"
    ORPHANED_ENTRY = "orphaned_entry"
    MISSING_SIGNATURE = "missing_signature"
    FONT_MISMATCH = "font_mismatch"
    ATS_PARSEABILITY = "ats_parseability"
    CID_MARKERS = "cid_markers"
    COMPILE_ERROR = "compile_error"


class CompilationIssue(BaseModel):
    """A single issue detected during compilation verification.

    Attributes:
        category: Which verification category this issue belongs to.
        severity: How critical this issue is.
        document: Which document (cv / cover_letter / both) has the issue.
        description: Human-readable description of the issue.
        line_ref: Optional LaTeX line number or section reference.
        fix_applied: Whether an automatic fix was attempted.
        fix_description: What fix was applied, if any.
        resolved: Whether the issue was resolved in a later iteration.
    """
    category: IssueCategory
    severity: IssueSeverity
    document: str = "cv"  # "cv" or "cover_letter"
    description: str
    line_ref: str | None = None
    fix_applied: bool = False
    fix_description: str | None = None
    resolved: bool = False


class CompileIteration(BaseModel):
    """A single attempt in the compilation loop.

    Attributes:
        iteration: 0-based iteration number.
        cv_pages: Page count achieved for CV in this iteration.
        cover_pages: Page count achieved for cover letter.
        issues: Issues detected in this iteration.
        fixes_applied: Descriptions of any automatic fixes applied.
        cv_latex: Updated CV LaTeX after fixes (for traceability).
        cover_latex: Updated cover letter LaTeX after fixes.
    """
    iteration: int
    cv_pages: int | None = None
    cover_pages: int | None = None
    issues: list[CompilationIssue] = Field(default_factory=list)
    fixes_applied: list[str] = Field(default_factory=list)
    cv_latex: str | None = None
    cover_latex: str | None = None


class CompileResult(BaseModel):
    """Final result of the compilation loop.

    Attributes:
        success: Whether compilation passed all critical checks.
        cv_pdf_path: Path to the final compiled CV PDF.
        cover_pdf_path: Path to the final compiled cover letter PDF.
        cv_tex_path: Path to the final CV .tex file.
        cover_tex_path: Path to the final cover letter .tex file.
        cv_pages: Final CV page count.
        cover_pages: Final cover letter page count.
        iterations: List of all compilation attempts.
        total_iterations: Total iterations used.
        final_issues: Remaining unresolved issues (warnings only).
        cv_latex: Final CV LaTeX content.
        cover_latex: Final cover letter LaTeX content.
    """
    success: bool
    cv_pdf_path: str | None = None
    cover_pdf_path: str | None = None
    cv_tex_path: str | None = None
    cover_tex_path: str | None = None
    cv_pages: int | None = None
    cover_pages: int | None = None
    iterations: list[CompileIteration] = Field(default_factory=list)
    total_iterations: int = 0
    final_issues: list[CompilationIssue] = Field(default_factory=list)
    cv_latex: str | None = None
    cover_latex: str | None = None
