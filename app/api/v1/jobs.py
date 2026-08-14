"""Job search endpoint — reads from ingested_jobs, triggers microservice if needed."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_max_or_admin
from app.schemas.jobs import (
    JobSearchRequest,
    JobSearchResponse,
    IngestStatusResponse,
)
from app.services.job_search import search_jobs, get_ingest_status

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/search", response_model=JobSearchResponse)
async def search(
    req: JobSearchRequest,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(get_db),
):
    return await search_jobs(db, req, user)


@router.get("/search/{ingest_job_id}/status", response_model=IngestStatusResponse)
async def ingest_status(
    ingest_job_id: str,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(get_db),
):
    return await get_ingest_status(db, ingest_job_id)
