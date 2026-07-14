"""Pydantic schemas for the rank skill.

Request/response shapes for ranking job postings against the candidate profile.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request schemas ─────────────────────────────────────────────────


class RankRequest(BaseModel):
    """Trigger a rank evaluation for new/unranked jobs.

    All fields are optional — defaults are sensible for a standard run.
    """

    focus_area: str | None = Field(
        None, description="Only rank jobs matching this focus area"
    )
    re_rank: bool = Field(
        False, description="Re-rank already-ranked jobs (useful after profile update)"
    )
    top_n: int = Field(
        5, description="Size of the shortlist to return", ge=1, le=50
    )


# ── Response schemas ────────────────────────────────────────────────


class RankEvaluationOut(BaseModel):
    """Detailed rank evaluation for a single job."""

    id: str
    job_posting_id: str
    user_id: str

    # Dimension scores
    technical_score: int
    experience_score: int
    behavioral_score: int
    career_score: int

    # Overall
    overall_score: int
    verdict: str

    # Location
    location_status: str

    # Deadline
    deadline: str | None
    deadline_urgent: bool

    # Insights
    strengths: list[str] | None
    gaps: list[str] | None
    missing_keywords: list[str] | None
    red_flags: list[str] | None

    # Language
    language: str | None

    # Timestamps
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RankedJobOut(BaseModel):
    """A ranked job with its evaluation (for shortlist responses)."""

    job: "JobPostingSummary"
    evaluation: RankEvaluationOut | None = None

    model_config = {"from_attributes": True}


class RankResult(BaseModel):
    """Result of a rank run."""

    ranked_count: int
    shortlist: list[RankedJobOut]
    below_threshold: int
    expired_or_vetoed: int
    message: str


# ── LLM output schema (for structured output parsing) ───────────────


class RankLLMOutput(BaseModel):
    """Expected JSON structure from the LLM for a single job evaluation."""

    technical_score: int = Field(ge=0, le=100)
    experience_score: int = Field(ge=0, le=100)
    behavioral_score: int = Field(ge=0, le=100)
    career_score: int = Field(ge=0, le=100)
    location_status: str = Field(pattern="^(PASS|FAIL|FLAG)$")
    deadline: str | None = None
    deadline_urgent: bool = False
    strengths: list[str] = Field(default_factory=list, max_length=3)
    gaps: list[str] = Field(default_factory=list, max_length=3)
    missing_keywords: list[str] = Field(default_factory=list, max_length=5)
    red_flags: list[str] = Field(default_factory=list, max_length=3)
    language: str | None = None


# Forward reference resolution
from app.schemas.scrape import JobPostingSummary

RankedJobOut.model_rebuild()
