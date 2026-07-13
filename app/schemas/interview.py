"""Pydantic schemas for the interview skill.

Request/response shapes for interview preparation.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Request schemas ─────────────────────────────────────────────────


class InterviewPrepRequest(BaseModel):
    """Trigger interview preparation for an application."""

    application_id: str = Field(..., description="The application to prepare for")
    stage: str = Field(
        ..., description="Interview stage: phone_screen, technical, case, final_round"
    )
    interview_date: str | None = Field(None, description="YYYY-MM-DD")
    interview_format: str | None = Field(None, description="phone, video, onsite")
    interviewer_names: list[str] | None = Field(None, description="Names and titles if known")

    @field_validator("stage")
    @classmethod
    def validate_stage(cls, v):
        valid_stages = {"phone_screen", "technical", "case", "final_round"}
        if v not in valid_stages:
            raise ValueError(f"stage must be one of: {', '.join(valid_stages)}")
        return v

    @field_validator("interview_format")
    @classmethod
    def validate_format(cls, v):
        if v is None:
            return v
        valid_formats = {"phone", "video", "onsite"}
        if v not in valid_formats:
            raise ValueError(f"interview_format must be one of: {', '.join(valid_formats)}")
        return v

    @field_validator("interview_date")
    @classmethod
    def validate_date(cls, v):
        if v is None:
            return v
        # Validate YYYY-MM-DD format
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("interview_date must be in YYYY-MM-DD format")
        return v


# ── Response schemas ────────────────────────────────────────────────


class CompanyResearchOut(BaseModel):
    """Company research for interview preparation."""

    mission: str | None = None
    values: list[str] = []
    recent_news: list[dict[str, str]] = []  # [{"title": "...", "url": "...", "date": "..."}]
    products: list[str] = []
    team_structure: str | None = None
    growth_signals: list[str] = []
    red_flags: list[str] = []


class ConversationHookOut(BaseModel):
    """Verified conversation hook for interview."""

    topic: str
    source_url: str
    why_relevant: str


class LikelyQuestionOut(BaseModel):
    """A likely interview question with metadata."""

    question: str
    source: str  # feedback, gaps, requirements, stage
    priority: str  # high, medium, low


class StarMappingOut(BaseModel):
    """Mapping of a question to a STAR example."""

    question: str
    star_example_id: str
    star_example_title: str


class NewStarDraftOut(BaseModel):
    """Draft STAR example for a question not covered by existing examples."""

    question: str
    draft_situation: str
    draft_task: str
    draft_action: str
    draft_result: str


class ConsistencyBriefOut(BaseModel):
    """Claims from CV/cover letter that interviewer will probe."""

    claim: str
    source: str  # cv, cover_letter
    why_probed: str


class ToughQuestionOut(BaseModel):
    """Customized tough question with answer."""

    question: str
    answer: str


class QuestionToAskOut(BaseModel):
    """Question for the candidate to ask the interviewer."""

    question: str
    category: str  # role, team, tech, culture
    why_ask: str


class LogisticsOut(BaseModel):
    """Interview logistics."""

    date: str | None = None
    format: str | None = None
    interviewer_names: list[str] = []
    phone_video_tips: list[str] = []


class InterviewPrepOut(BaseModel):
    """Complete interview preparation pack."""

    id: str
    application_id: str
    user_id: str

    # Stage info
    stage: str
    interview_date: str | None
    interview_format: str | None
    interviewer_names: list[str] | None

    # Company research
    company_research: CompanyResearchOut | None
    conversation_hooks: list[ConversationHookOut] = []

    # Likely questions
    likely_questions: list[LikelyQuestionOut] = []

    # STAR mapping
    star_mapping: list[StarMappingOut] = []
    new_star_drafts: list[NewStarDraftOut] = []

    # Consistency brief
    consistency_brief: list[ConsistencyBriefOut] = []

    # Tough questions
    tough_questions: list[ToughQuestionOut] = []

    # Questions to ask
    questions_to_ask: list[QuestionToAskOut] = []

    # Logistics
    logistics: LogisticsOut | None

    # Mock transcript (optional)
    mock_transcript: str | None = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InterviewPrepSummaryOut(BaseModel):
    """Lightweight interview prep for list views."""

    id: str
    application_id: str
    stage: str
    interview_date: str | None
    interview_format: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── LLM output schemas ──────────────────────────────────────────────


class CompanyResearchLLMOutput(BaseModel):
    """Structured output for company research."""

    mission: str | None = None
    values: list[str] = []
    recent_news: list[dict[str, str]] = []
    products: list[str] = []
    team_structure: str | None = None
    growth_signals: list[str] = []
    red_flags: list[str] = []


class ConversationHookLLMOutput(BaseModel):
    """Structured output for conversation hooks."""

    topic: str
    source_url: str
    why_relevant: str


class LikelyQuestionsLLMOutput(BaseModel):
    """Structured output for likely questions."""

    questions: list[LikelyQuestionOut] = []


class StarMappingLLMOutput(BaseModel):
    """Structured output for STAR mapping."""

    mappings: list[StarMappingOut] = []


class NewStarDraftsLLMOutput(BaseModel):
    """Structured output for new STAR drafts."""

    drafts: list[NewStarDraftOut] = []


class ConsistencyBriefLLMOutput(BaseModel):
    """Structured output for consistency brief."""

    claims: list[ConsistencyBriefOut] = []


class ToughQuestionsLLMOutput(BaseModel):
    """Structured output for tough questions."""

    questions: list[ToughQuestionOut] = []


class QuestionsToAskLLMOutput(BaseModel):
    """Structured output for questions to ask."""

    questions: list[QuestionToAskOut] = []


class LogisticsLLMOutput(BaseModel):
    """Structured output for logistics."""

    date: str | None = None
    format: str | None = None
    interviewer_names: list[str] = []
    phone_video_tips: list[str] = []