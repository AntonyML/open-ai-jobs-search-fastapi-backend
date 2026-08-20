"""Pydantic schemas for the upskill skill.

Request/response shapes for identifying skill gaps and generating a learning plan.
"""

from datetime import datetime

from pydantic import BaseModel, Field

# ── Request schemas ─────────────────────────────────────────────────


class UpskillRequest(BaseModel):
    """Trigger an upskill analysis.

    All fields are optional — defaults scan all tracked jobs in aggregate mode.
    """

    mode: str = Field(
        "aggregate",
        description="Analysis mode: 'aggregate' (all tracked jobs) or 'targeted' (single job URL)",
        pattern="^(aggregate|targeted)$",
    )
    target_job_url: str | None = Field(None, description="Job posting URL for targeted mode")
    target_job_posting_id: str | None = Field(None, description="Job posting ID from DB for targeted mode")


# ── Response schemas ────────────────────────────────────────────────


class HardSkillGapOut(BaseModel):
    """A hard skill gap identified in Pass 1."""

    skill: str
    type: str = "hard"
    priority: str = Field(pattern="^(Critical|High|Medium|Low)$")
    source_jobs: list[str] = []
    frequency: int
    fit_weight: float


class SynthesizedGapOut(BaseModel):
    """A synthesized gap identified in Pass 2 (LLM synthesis)."""

    skill: str
    type: str = Field(pattern="^(domain|soft|tooling|credential)$")
    priority: str = Field(pattern="^(Critical|High|Medium|Low)$")
    source: str = "LLM synthesis"
    evidence: str


class GapHeatmapOut(BaseModel):
    """A combined gap entry in the heatmap."""

    skill: str
    type: str = Field(pattern="^(hard|domain|soft|tooling|credential)$")
    priority: str = Field(pattern="^(Critical|High|Medium|Low)$")
    gap_source: str


class LearningResourceOut(BaseModel):
    """A study resource for a skill."""

    title: str
    url: str
    format: str = Field(pattern="^(course|video|article|certification)$")
    duration_hours: int | None = None
    cost: str = Field(pattern="^(free|paid)$")
    quality_score: int = Field(ge=1, le=10)


class LearningPlanItemOut(BaseModel):
    """A single item in the learning plan."""

    skill: str
    type: str = Field(pattern="^(hard|domain|soft|tooling|credential)$")
    priority: str = Field(pattern="^(Critical|High|Medium|Low)$")
    resources: list[LearningResourceOut] = []
    study_order: int
    prerequisites: list[str] = []
    estimated_weeks: int


class UpskillOut(BaseModel):
    """Complete upskill analysis result."""

    id: str
    user_id: str
    candidate_id: str

    mode: str = Field(pattern="^(aggregate|targeted)$")
    target_job_posting_id: str | None = None
    target_job_url: str | None = None

    hard_skill_gaps: list[HardSkillGapOut] = []
    synthesized_gaps: list[SynthesizedGapOut] = []
    gap_heatmap: list[GapHeatmapOut] = []
    learning_plan: list[LearningPlanItemOut] = []

    status: str
    error_message: str | None = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UpskillSummaryOut(BaseModel):
    """Lightweight upskill for list views."""

    id: str
    user_id: str
    candidate_id: str
    mode: str
    status: str
    gaps_found: int
    learning_plan_items: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── LLM output schemas ──────────────────────────────────────────────


class HardSkillGapsLLMOutput(BaseModel):
    """Structured output for Pass 1 hard skill gap extraction."""

    gaps: list[HardSkillGapOut] = []


class SynthesizedGapsLLMOutput(BaseModel):
    """Structured output for Pass 2 LLM synthesis."""

    gaps: list[SynthesizedGapOut] = []


class GapHeatmapLLMOutput(BaseModel):
    """Structured output for combining Pass 1 + Pass 2 into heatmap."""

    heatmap: list[GapHeatmapOut] = []


class LearningPlanLLMOutput(BaseModel):
    """Structured output for learning plan generation."""

    plan: list[LearningPlanItemOut] = []
