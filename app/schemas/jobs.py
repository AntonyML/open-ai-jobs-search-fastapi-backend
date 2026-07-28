"""Schemas for job search (reads from ingested_jobs, microservice feeds it)."""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class JobSearchRequest(BaseModel):
    keywords: str = Field(..., min_length=2, max_length=200)
    location: Optional[str] = Field(None, max_length=200)
    limit: int = Field(50, ge=1, le=100)


class JobOut(BaseModel):
    id: str
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    salary: Optional[str] = None
    ingested_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobSearchResponse(BaseModel):
    jobs: list[JobOut]
    count: int
    fresh: bool
    ingest_job_id: Optional[str] = None
    message: Optional[str] = None


class IngestStatusResponse(BaseModel):
    status: str
    result_count: Optional[int] = None
    error: Optional[str] = None
