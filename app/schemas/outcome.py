"""Pydantic schemas for the outcome skill.

Request/response shapes for recording job application outcomes.
"""

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


# ── Request schemas ─────────────────────────────────────────────────


class OutcomeCreate(BaseModel):
    """Create or update an application outcome."""

    application_id: str = Field(..., description="The application to record outcome for")
    status: str = Field(
        ...,
        description=(
            "Progress update: interview_invited, phone_screen_completed, "
            "technical_completed, case_completed, final_round_completed, "
            "offer_received. Resolution: hired, offer_declined, rejected, "
            "no_response, interview_only, withdrawn"
        ),
    )
    date_resolved: str | None = Field(
        None, description="YYYY-MM-DD (only for final resolutions)"
    )
    phone_screen_date: str | None = Field(None, description="YYYY-MM-DD")
    technical_date: str | None = Field(None, description="YYYY-MM-DD")
    case_date: str | None = Field(None, description="YYYY-MM-DD")
    final_round_date: str | None = Field(None, description="YYYY-MM-DD")
    offer_received_date: str | None = Field(None, description="YYYY-MM-DD")
    notes: str | None = Field(None, description="Feedback, what to do differently, signals")
    lessons_learned: str | None = Field(None, description="Candidate's reflection on the process")
    valued_signals: list[str] | None = Field(None, description="What the company valued")


class OutcomeUpdate(BaseModel):
    """Partial update for an outcome."""

    status: str | None = None
    date_resolved: str | None = None
    phone_screen_date: str | None = None
    technical_date: str | None = None
    case_date: str | None = None
    final_round_date: str | None = None
    offer_received_date: str | None = None
    notes: str | None = None
    lessons_learned: str | None = None
    valued_signals: list[str] | None = None


# ── Response schemas ────────────────────────────────────────────────


class OutcomeOut(BaseModel):
    """Outcome response."""

    id: str
    user_id: str
    application_id: str

    status: str
    date_resolved: str | None

    phone_screen_date: str | None
    technical_date: str | None
    case_date: str | None
    final_round_date: str | None
    offer_received_date: str | None

    notes: str | None
    lessons_learned: str | None
    valued_signals: list[str] | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OutcomeSummaryOut(BaseModel):
    """Lightweight outcome for list views."""

    id: str
    application_id: str
    status: str
    date_resolved: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Tracker row schema (for job_search_tracker.csv sync) ────────────


def _empty_str_to_none(v: Any) -> Any:
    """Convert empty strings (from CSV) to None."""
    if v == "":
        return None
    return v


class TrackerRowOut(BaseModel):
    """A row in the job search tracker (synced with outcome)."""

    date: str
    company: str
    sector: str | None
    role: str
    role_type: str | None
    channel: str | None
    status: str
    contact_person: str | None
    fit_rating: Annotated[int | None, BeforeValidator(_empty_str_to_none)]
    notes: str | None
    cv_file: str | None
    cover_letter_file: str | None
    source: str | None

    model_config = {"from_attributes": True}


# ── LLM output schemas ──────────────────────────────────────────────


class OutcomeLLMOutput(BaseModel):
    """Structured output from LLM for outcome processing."""

    status: str
    date_resolved: str | None = None
    phone_screen_date: str | None = None
    technical_date: str | None = None
    case_date: str | None = None
    final_round_date: str | None = None
    offer_received_date: str | None = None
    notes: str | None = None
    lessons_learned: str | None = None
    valued_signals: list[str] = []


# ═══════════════════════════════════════════════════════════════════
# CALIBRATION SCHEMAS  (FASE 7 — fit calibration)
# ═══════════════════════════════════════════════════════════════════


class FunnelMetrics(BaseModel):
    """Conversion funnel metrics for a user's job search."""

    total_applications: int = 0
    interviews: int = 0
    offers: int = 0
    hired: int = 0
    rejected: int = 0
    no_response: int = 0
    withdrawn: int = 0
    in_progress: int = 0

    # Conversion rates
    application_to_interview_pct: float = 0.0
    interview_to_offer_pct: float = 0.0
    offer_to_hired_pct: float = 0.0
    overall_success_pct: float = 0.0


class CalibrationKeyword(BaseModel):
    """A keyword/skill with its correlation to outcome success."""

    keyword: str
    present_in_count: int = 0
    interview_rate: float = 0.0  # % of apps with this keyword that got interviews
    hire_rate: float = 0.0  # % of apps with this keyword that got hired
    avg_score: float = 0.0  # average rank score for jobs with this keyword
    correlation: str = "neutral"  # "positive", "negative", "neutral"


class CalibrationInsight(BaseModel):
    """A single actionable insight from the calibration analysis."""

    category: str  # "keyword", "company", "role_type", "location", "template"
    insight: str
    recommendation: str
    impact: str  # "high", "medium", "low"


class CalibrationReport(BaseModel):
    """Full calibration report generated from outcome data."""

    funnel: FunnelMetrics
    top_keywords: list[CalibrationKeyword] = []
    bottom_keywords: list[CalibrationKeyword] = []
    insights: list[CalibrationInsight] = []
    data_points: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))