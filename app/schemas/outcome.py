"""Pydantic schemas for the outcome skill.

Request/response shapes for recording job application outcomes.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
    fit_rating: int | None
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