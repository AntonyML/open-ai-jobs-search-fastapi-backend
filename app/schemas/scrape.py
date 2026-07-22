"""Pydantic schemas for the scrape skill.

Request/response shapes for scraping job postings via the Bun/TS CLIs.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


# ── Request schemas ─────────────────────────────────────────────────


class ScrapeRequest(BaseModel):
    """Trigger a scrape run.

    All fields are optional — defaults are sensible for a standard run.
    """

    focus_area: str | None = Field(
        None, description="Narrow the search to a specific area, e.g. 'data science'"
    )
    keywords: list[str] | None = Field(
        None, description="Specific skill/keyword tags to search for"
    )
    target_titles: list[str] | None = Field(
        None, description="Job titles to target (primary search terms)"
    )
    seniority: str | None = Field(
        None,
        description="Experience level: junior, mid, senior, lead, manager, director, executive",
    )
    location: str | None = Field(
        None, description="Location to search in (e.g. 'San Jose, Costa Rica')"
    )
    remote: str | None = Field(
        None,
        description="Workplace type filter: 'remote', 'hybrid', 'onsite', or None for all",
    )
    broad: bool = Field(
        False, description="Run all search categories instead of just the top 3"
    )
    portals: list[str] | None = Field(
        None,
        description=(
            "Specific portals to query. If omitted, all installed scrapers are used. "
            "Valid values: linkedin, freehire, jobbank, jobdanmark, jobindex, jobnet"
        ),
    )
    jobage_days: int = Field(
        14, description="Only include postings from the last N days", ge=1, le=9999
    )
    limit_per_portal: int = Field(
        20, description="Max results per portal", ge=1, le=100
    )


# ── Response schemas ───────────────────────────────────────────────


class JobPostingOut(BaseModel):
    """A single job posting as returned by the API."""

    id: str
    portal: str
    external_id: str
    title: str
    company: str | None
    location: str | None
    url: HttpUrl | None
    posting_date: str | None
    deadline: str | None
    description: str | None
    requirements: list[str] | None
    employment_type: str | None
    language: str | None
    status: str
    rank_score: float | None
    rank_verdict: str | None
    rank_date: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobPostingSummary(BaseModel):
    """Lightweight job posting for list views."""

    id: str
    portal: str
    title: str
    company: str | None
    location: str | None
    url: HttpUrl | None
    posting_date: str | None
    status: str
    rank_score: float | None
    rank_verdict: str | None

    model_config = {"from_attributes": True}


class ScrapeRunOut(BaseModel):
    """Result of a scrape run."""

    id: str
    triggered_by: str
    focus_area: str | None
    broad: bool
    portals_queried: list[str]
    external_sources: list[str] | None
    external_results: list[dict] | None
    jobs_found: int
    jobs_new: int
    jobs_expired: int
    status: str
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScrapeResult(BaseModel):
    """Immediate response when a scrape is triggered."""

    run_id: str
    status: str
    portals_queried: list[str]
    jobs_found: int
    jobs_new: int
    message: str


# ── Scraper CLI output shape (parsed from Bun/TS stdout) ────────────


class ScraperResultItem(BaseModel):
    """A single result item from a scraper CLI's JSON output."""

    id: str | None = None
    title: str
    company: str | None = None
    location: str | None = None
    url: str | None = None
    date: str | None = None


class ScraperOutput(BaseModel):
    """The JSON shape emitted by all scraper CLIs on stdout."""

    meta: dict[str, Any] | None = None
    results: list[ScraperResultItem] = []