"""Pydantic schemas for the rank skill.

Request/response shapes for ranking job postings against the candidate profile.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.salary import SalaryBenchmark


# ── Dimension score (Fase 4) ─────────────────────────────────────────


class DimensionScore(BaseModel):
    """Score estructurado para una dimensión del ranking.

    Cada dimensión incluye score numérico, nivel de confianza,
    y evidencia textual que justifica el score.
    """

    score: int = Field(ge=0, le=100)
    confidence: str = Field(pattern="^(high|medium|low|unknown)$")
    evidence: list[str] = Field(default_factory=list)


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

    # Structured dimensions (Fase 4)
    technical_fit: DimensionScore | None = None
    relevant_experience: DimensionScore | None = None
    constraints_fit: DimensionScore | None = None
    career_alignment: DimensionScore | None = None
    behavioral_fit: DimensionScore | None = None

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
    """A ranked job with its evaluation (for shortlist responses).

    Includes optional salary benchmark when the user has uploaded
    salary data and the company is found in it.
    """

    job: "JobPostingSummary"
    evaluation: RankEvaluationOut | None = None

    # Salary benchmark (typed, only present when salary data is available
    # AND the company is found in the user's data)
    salary: "SalaryBenchmark | None" = None

    model_config = {"from_attributes": True}


class RankResult(BaseModel):
    """Result of a rank run."""

    ranked_count: int
    shortlist: list[RankedJobOut]
    below_threshold: int
    expired_or_vetoed: int
    message: str

    # Salary data status (present when user has uploaded salary data)
    salary_data_available: bool = False
    salary_data_company_count: int = 0


# ── LLM output schemas ───────────────────────────────────────────────


class RankQualitativeOutput(BaseModel):
    """Nuevo contrato LLM (Fase 5) — solo campos cualitativos.

    technical_score, experience_score, location_status, deadline,
    missing_keywords y language son deterministas (Fase 4) y se
    calculan server-side — el LLM ya no los recibe ni produce.
    """

    behavioral_score: int = Field(ge=0, le=100)
    career_score: int = Field(ge=0, le=100)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    gaps: list[str] = Field(default_factory=list, max_length=5)
    red_flags: list[str] = Field(default_factory=list, max_length=3)
    confidence: str = Field(default="medium", pattern="^(low|medium|high)$")


class RankLLMOutput(BaseModel):
    """Legacy schema — kept for backward compat.  Use RankQualitativeOutput."""
    technical_score: int = Field(ge=0, le=100, default=0)
    experience_score: int = Field(ge=0, le=100, default=0)
    behavioral_score: int = Field(ge=0, le=100)
    career_score: int = Field(ge=0, le=100)
    location_status: str = Field(default="FLAG", pattern="^(PASS|FAIL|FLAG)$")
    deadline: str | None = None
    deadline_urgent: bool = False
    strengths: list[str] = Field(default_factory=list, max_length=5)
    gaps: list[str] = Field(default_factory=list, max_length=5)
    missing_keywords: list[str] = Field(default_factory=list, max_length=5)
    red_flags: list[str] = Field(default_factory=list, max_length=3)
    language: str | None = None
    confidence: str = Field(default="medium", pattern="^(low|medium|high)$")


# Forward reference resolution
from app.schemas.scrape import JobPostingSummary

RankedJobOut.model_rebuild()
