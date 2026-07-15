"""Pydantic schemas for salary benchmarking.

Request/response shapes for uploading salary data and looking up
salary benchmarks during the ranking flow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Upload / Storage schemas ────────────────────────────────────────


class SalaryCompanyEntry(BaseModel):
    """A single company entry in salary data."""

    company: str
    city: str | None = None
    categories: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class SalaryMetadata(BaseModel):
    """Metadata block for salary data file."""

    source: str = "unknown"
    index_label: str = "Index"
    index_baseline: float = 100.0
    baseline_description: str = ""


class SalaryDataUpload(BaseModel):
    """Payload for uploading salary data (JSON format)."""

    companies: list[SalaryCompanyEntry] = Field(
        ..., description="Array of company salary entries"
    )
    metadata: SalaryMetadata = Field(
        default_factory=SalaryMetadata,
        description="Metadata about the salary data source",
    )


class SalaryUploadResponse(BaseModel):
    """Response after uploading salary data."""

    status: str = "ok"
    company_count: int
    message: str


# ── Lookup / Benchmark schemas ──────────────────────────────────────


class SalaryBenchmark(BaseModel):
    """Salary benchmark for a single job posting.

    Included in the rank response when salary data is available.
    All fields are optional — if salary data is missing or the
    company is not found, the entire block is omitted (not null).
    """

    company_name: str
    match_confidence: int = Field(
        ge=0, le=100, description="Fuzzy match score 0-100"
    )
    city: str | None = None

    # Salary estimate from the user's data
    salary_estimate: float | None = Field(
        None, description="Estimated salary for this role/company"
    )
    market_median: float | None = Field(
        None, description="Market median for this role/location"
    )
    salary_delta_pct: float | None = Field(
        None, description="(salary_estimate - market_median) / market_median * 100"
    )
    index_value: float | None = Field(
        None, description="Index value from salary data (e.g. 105.5 = 5.5% above baseline)"
    )

    # Source
    data_source: str = "user_upload"  # "user_upload" | "global_file"


class SalaryDataStatus(BaseModel):
    """Status of the user's salary data."""

    has_data: bool
    company_count: int = 0
    source: str | None = None
    uploaded_at: datetime | None = None
    companies: list[SalaryCompanyEntry] | None = None
