"""Scrape router — endpoints for triggering and querying scrapes."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.session import get_db as _get_db
from app.schemas.scrape import (
    JobPostingOut,
    JobPostingSummary,
    ScrapeRequest,
    ScrapeResult,
    ScrapeRunOut,
)
from app.services import scrape

router = APIRouter(prefix="/scrape", tags=["scrape"])


@router.post(
    "/",
    response_model=ScrapeResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_scrape(
    payload: ScrapeRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Trigger a scrape run.

    The scrape runs synchronously in the request (for now).  For long-running
    scrapes, consider moving to a background task in a future iteration.
    """
    run = await scrape.execute_scrape(
        db=db,
        user_id=user["sub"],
        focus_area=payload.focus_area,
        broad=payload.broad,
        portals=payload.portals,
        jobage_days=payload.jobage_days,
        limit_per_portal=payload.limit_per_portal,
        triggered_by="manual",
    )

    return ScrapeResult(
        run_id=run.id,
        status=run.status,
        portals_queried=run.portals_queried,
        jobs_found=run.jobs_found,
        jobs_new=run.jobs_new,
        message=f"Scrape completed: {run.jobs_new} new jobs from {len(run.portals_queried)} portals",
    )


@router.get("/runs", response_model=list[ScrapeRunOut])
async def list_scrape_runs(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List scrape run history for the authenticated user."""
    return await scrape.list_scrape_runs(db, user["sub"], limit=limit)


@router.get("/jobs", response_model=list[JobPostingSummary])
async def list_jobs(
    status_filter: str | None = Query(None, alias="status"),
    portal: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List job postings for the authenticated user with optional filters."""
    return await scrape.list_job_postings(
        db=db,
        user_id=user["sub"],
        status_filter=status_filter,
        portal=portal,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=JobPostingOut)
async def get_job(
    job_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get a single job posting by ID."""
    return await scrape.get_job_posting(db, job_id, user["sub"])