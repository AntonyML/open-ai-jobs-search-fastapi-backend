"""Pydantic schemas for the add-portal skill.

Request/response shapes for generating a new job portal search skill.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request schemas ─────────────────────────────────────────────────


class AddPortalRequest(BaseModel):
    """Trigger generation of a new job portal search skill."""

    portal_url: str = Field(..., description="The job board's public site URL")
    skill_name: str = Field(
        ..., description="Kebab-case name suffixed with -search (e.g., 'seek-search')"
    )
    market_and_language: str = Field(
        ..., description="Country/region and language of the portal"
    )
    test_query: str = Field(
        ..., description="A realistic job title/skill to test the skill with"
    )


# ── Response schemas ────────────────────────────────────────────────


class PortalSkillOut(BaseModel):
    """Generated portal skill metadata (filesystem-backed, no ORM)."""

    skill_name: str
    portal_url: str
    market_and_language: str
    test_query: str | None = None
    status: str
    error_message: str | None = None
    test_result: dict | None = None
    investigation: dict | None = None
    created_at: datetime
    updated_at: datetime


class PortalSkillSummaryOut(BaseModel):
    """Lightweight portal skill for list views."""

    skill_name: str
    portal_url: str
    market_and_language: str
    status: str
    created_at: datetime


# ── LLM output schemas ──────────────────────────────────────────────


class PortalInvestigationLLMOutput(BaseModel):
    """Structured output for portal investigation."""

    search_endpoint: str
    query_param: str
    location_param: str | None = None
    posting_age_param: str | None = None
    pagination_param: str | None = None
    result_fields: dict[str, str]  # field_name -> JSON path
    detail_endpoint: str | None = None
    detail_fields: dict[str, str] | None = None
    auth_required: bool = False
    robots_txt_allows: bool = True
    terms_allows: bool = True
    notes: str = ""


class PortalSkillGenerationLLMOutput(BaseModel):
    """Structured output for portal skill code generation."""

    cli_ts: str  # TypeScript code for cli.ts
    search_ts: str  # TypeScript code for commands/search.ts
    detail_ts: str  # TypeScript code for commands/detail.ts
    helpers_ts: str  # TypeScript code for helpers.ts
    package_json: str  # package.json content
    tsconfig_json: str  # tsconfig.json content
    readme_md: str  # README.md content
    test_helpers_ts: str  # TypeScript code for tests/helpers.ts