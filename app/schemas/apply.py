"""Pydantic schemas for the apply skill.

Request/response shapes for generating tailored CV and cover letter.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request schemas ─────────────────────────────────────────────────


class ApplyRequest(BaseModel):
    """Trigger CV + cover letter generation for a ranked job."""

    job_posting_id: str = Field(..., description="The job posting to apply to")
    # Optional: override the rank evaluation to use
    rank_evaluation_id: str | None = Field(
        None, description="Specific rank evaluation to use (defaults to latest)"
    )
    # Optional: custom template names
    cv_template: str | None = Field("moderncv-banking", description="CV template to use")
    cover_letter_template: str | None = Field("cover-cls", description="Cover letter template to use")


# ── Response schemas ────────────────────────────────────────────────


class TailoredExperienceEntry(BaseModel):
    """A single tailored experience entry using X-Y-Z formula."""

    title: str
    company: str
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None
    bullets: list[str] = Field(..., description="X-Y-Z formatted bullets")


class IncorporatedKeyword(BaseModel):
    """A missing keyword that was incorporated into the tailored CV."""

    keyword: str
    where_incorporated: str  # e.g., "experience bullet 2", "skills section"
    original_context: str | None = None  # how it appeared in job posting


class AddressedRedFlag(BaseModel):
    """A red flag that was addressed in the tailored CV."""

    red_flag: str
    how_addressed: str


class ApplicationOut(BaseModel):
    """Generated application with tailored content and file paths."""

    id: str
    user_id: str
    job_posting_id: str
    rank_evaluation_id: str

    # Tailored content
    tailored_experience: list[TailoredExperienceEntry] | None = None
    incorporated_keywords: list[IncorporatedKeyword] | None = None
    addressed_red_flags: list[AddressedRedFlag] | None = None

    # Generated files
    cv_tex_path: str | None = None
    cv_pdf_path: str | None = None
    cover_letter_tex_path: str | None = None
    cover_letter_pdf_path: str | None = None

    # Compilation status
    cv_compiled: bool = False
    cv_pages: int | None = None
    cover_letter_compiled: bool = False
    cover_letter_pages: int | None = None

    # Templates used
    cv_template: str
    cover_letter_template: str
    language: str

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApplyResult(BaseModel):
    """Result of an apply run."""

    application_id: str
    cv_compiled: bool
    cv_pages: int | None
    cover_letter_compiled: bool
    cover_letter_pages: int | None
    message: str


class ApplicationStatusOut(BaseModel):
    """Lightweight status response for polling."""

    id: str
    pipeline_stage: str
    progress_pct: int
    current_action: str
    review_issues_count: int = 0
    cv_compiled: bool = False
    cover_letter_compiled: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── LLM output schemas ──────────────────────────────────────────────


class TailoredExperienceLLMOutput(BaseModel):
    """LLM output for tailored experience section."""

    experience: list[TailoredExperienceEntry] = Field(max_length=10)


class CoverLetterLLMOutput(BaseModel):
    """LLM output for cover letter content."""

    opening_paragraph: str
    body_paragraphs: list[str] = Field(max_length=4)
    company_connection_paragraph: str
    personal_fit_paragraph: str
    closing_paragraph: str


# ── Drafter-Reviewer schemas ────────────────────────────────────────


class ReviewIssue(BaseModel):
    """A single issue identified by the reviewer agent."""

    type: str = Field(
        ...,
        description="One of: missing_keyword, generic_bullet, fabricated_claim, weak_framing, inconsistency, factual_error, formatting",
    )
    description: str = Field(..., description="Clear description of the issue")
    severity: str = Field(..., description="high, medium, or low")
    location: str = Field(..., description="cv, cover_letter, or both")
    suggestion: str | None = Field(None, description="How to fix this issue")


class ReviewFeedback(BaseModel):
    """Structured feedback from the reviewer agent.

    The reviewer examines the full rendered LaTeX of both documents
    and provides actionable feedback. Temperature 0 for reproducibility.
    """

    overall_assessment: str = Field(..., description="2-3 sentence summary of document quality")
    passes: list[str] = Field(default_factory=list, description="Things done well")
    issues: list[ReviewIssue] = Field(default_factory=list, description="Issues to fix")
    missed_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords from job posting still absent after addressing rank evaluation",
    )
    strong_recommendations: list[str] = Field(
        default_factory=list,
        description="Top 3 changes that would most improve the application (ordered by impact)",
    )


class ReviseAction(BaseModel):
    """A single revision action taken based on review feedback."""

    issue_type: str = Field(..., description="The type of issue addressed")
    description: str = Field(..., description="What was changed and why")


class ReviseResult(BaseModel):
    """Result of applying review feedback to improve the drafts.

    The revised content replaces the original draft for final compilation.
    """

    changes_made: list[ReviseAction] = Field(
        default_factory=list,
        description="List of changes made in response to reviewer feedback",
    )
    remaining_concerns: list[str] = Field(
        default_factory=list,
        description="Issues that could not be fully addressed (honest limitations)",
    )
    overall_quality_improvement: str = Field(
        ...,
        description="Brief statement of how the documents improved after revision",
    )