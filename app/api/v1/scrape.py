"""Scrape router — endpoints for triggering and querying scrapes."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_locale
from app.core.i18n.locale import t
from app.db.session import get_db as _get_db
from app.schemas.scrape import (
    JobPostingOut,
    JobPostingSummary,
    ScrapeRequest,
    ScrapeResult,
    ScrapeRunOut,
)
from app.services import scrape
from app.services.tiers import get_tier_limits

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
    locale: str = Depends(get_locale),
):
    """Trigger a scrape run.

    Free-tier users:
    - Limited to 1 site (``max_scrape_sites=1``).
    - Limited to 5 jobs per portal (``max_scrape_jobs=5``).
    """
    limits = get_tier_limits(user.get("tier", "free"))

    # Free-tier limits
    is_free = user.get("tier") == "free" or user.get("tier") is None
    portals = payload.portals
    if is_free:
        if portals and len(portals) > limits["max_scrape_sites"]:
            portals = portals[: limits["max_scrape_sites"]]
    broad = False if is_free else payload.broad

    limit_per_portal = min(payload.limit_per_portal, limits["max_scrape_jobs"])

    run = await scrape.execute_scrape(
        db=db,
        user_id=user["sub"],
        focus_area=payload.focus_area,
        keywords=payload.keywords,
        target_titles=payload.target_titles,
        seniority=payload.seniority,
        location=payload.location,
        remote=payload.remote,
        broad=broad,
        portals=portals,
        jobage_days=payload.jobage_days,
        limit_per_portal=limit_per_portal,
        triggered_by="manual",
    )

    return ScrapeResult(
        run_id=run.id,
        status=run.status,
        portals_queried=run.portals_queried,
        jobs_found=run.jobs_found,
        jobs_new=run.jobs_new,
        message=t("scrape.completed", locale, found=run.jobs_found, new=run.jobs_new),
    )


@router.get("/runs", response_model=list[ScrapeRunOut])
async def list_scrape_runs(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List scrape run history for the authenticated user."""
    return await scrape.list_scrape_runs(db, user["sub"], limit=limit)


@router.get("/runs/{run_id}", response_model=ScrapeRunOut)
async def get_scrape_run(
    run_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get a single scrape run by ID (for polling live status)."""
    from app.exceptions import NotFoundError

    run = await scrape.get_scrape_run(db, run_id, user["sub"])
    if run is None:
        raise NotFoundError("Scrape run not found.")
    return run


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