"""Pydantic schemas for the expand skill.

Request/response shapes for competency expansion from documents and online presence.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ── Request schemas ─────────────────────────────────────────────────


class ExpandRequest(BaseModel):
    """Trigger a competency expansion run.

    All fields are optional — defaults scan all available sources.
    """

    scan_cv: bool = Field(True, description="Scan documents/cv/ folder")
    scan_linkedin: bool = Field(True, description="Scan documents/linkedin/ folder")
    scan_diplomas: bool = Field(True, description="Scan documents/diplomas/ folder")
    scan_references: bool = Field(True, description="Scan documents/references/ folder")
    scan_github: bool = Field(True, description="Scan GitHub profile from candidate profile")
    scan_other_urls: bool = Field(True, description="Scan other URLs from candidate profile (portfolio, Kaggle, etc.)")


# ── Response schemas ────────────────────────────────────────────────


class ExperienceItemOut(BaseModel):
    """An experience item discovered during scanning."""

    id: str
    source: str  # cv, linkedin, diplomas, references, github, other_url
    type: str  # course, certification, job_bullet, project, volunteer, repo
    title: str
    description: str | None = None
    date: str | None = None
    source_file: str | None = None

    model_config = {"from_attributes": True}


class EnrichedCompetencyOut(BaseModel):
    """Competencies discovered for an experience item via web search."""

    experience_item_id: str
    competencies: list[str]
    source: str  # direct_lookup, inferred
    source_urls: list[str] = []

    model_config = {"from_attributes": True}


class ProposedAdditionOut(BaseModel):
    """A competency proposed for addition to the candidate profile."""

    category: str  # programming_ml, domain_expertise, software_tools
    skill: str
    proficiency: str | None = None
    evidence: str
    source: str

    model_config = {"from_attributes": True}


class CompetencyExpansionOut(BaseModel):
    """Complete competency expansion result."""

    id: str
    user_id: str
    candidate_id: str

    # Sources scanned
    scanned_cv: bool
    scanned_linkedin: bool
    scanned_diplomas: bool
    scanned_references: bool
    scanned_github: bool
    scanned_other_urls: bool

    # Discovered items
    experience_items: list[ExperienceItemOut] = []
    enriched_competencies: list[EnrichedCompetencyOut] = []
    proposed_additions: list[ProposedAdditionOut] = []

    # Status
    status: str
    error_message: str | None = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CompetencyExpansionSummaryOut(BaseModel):
    """Lightweight expansion for list views."""

    id: str
    candidate_id: str
    status: str
    items_found: int
    competencies_enriched: int
    proposed_additions: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── LLM output schemas ──────────────────────────────────────────────


class ExperienceItemLLMOutput(BaseModel):
    """Structured output for experience item extraction."""

    items: list[dict[str, Any]] = []


class EnrichedCompetency(BaseModel):
    """A single competency enrichment for an experience item."""

    experience_item_id: str
    competencies: list[str] = []
    source: str = "inferred"
    source_urls: list[str] = []


class EnrichedCompetenciesLLMOutput(BaseModel):
    """Structured output for competency enrichment."""

    enrichments: list[EnrichedCompetency] = []


class ProposedAddition(BaseModel):
    """A proposed addition to the candidate profile."""

    category: str
    item: dict[str, Any] | str = {}
    reason: str = ""


class ProposedAdditionsLLMOutput(BaseModel):
    """Structured output for proposed profile additions."""

    additions: list[ProposedAddition] = []
