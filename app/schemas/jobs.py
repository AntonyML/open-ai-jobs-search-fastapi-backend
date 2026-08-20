"""Schemas for job search (reads from ingested_jobs, microservice feeds it)."""

from datetime import datetime

from pydantic import BaseModel, Field


class JobSearchRequest(BaseModel):
    keywords: str = Field(..., min_length=2, max_length=200)
    location: str | None = Field(None, max_length=200)
    limit: int = Field(50, ge=1, le=100)


class JobOut(BaseModel):
    id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str | None = None
    description: str | None = None
    salary: str | None = None
    ingested_at: datetime | None = None

    model_config = {"from_attributes": True}


class JobSearchResponse(BaseModel):
    jobs: list[JobOut]
    count: int
    fresh: bool
    ingest_job_id: str | None = None
    message: str | None = None


class IngestStatusResponse(BaseModel):
    status: str
    result_count: int | None = None
    error: str | None = None
