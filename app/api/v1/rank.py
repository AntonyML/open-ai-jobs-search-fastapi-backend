"""Rank router — endpoints for ranking job postings.

Fase 6: POST /rank/ supports Idempotency-Key header, returns
{job_id, status, total_jobs, accepted_jobs}.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_locale, require_max_or_admin
from app.core.i18n.locale import t
from app.core.settings import get_settings
from app.db.models import ExecutionJob
from app.db.session import get_db as _get_db
from app.schemas.rank import RankRequest, RankResult
from app.schemas.rank import RankEvaluationOut as RankEvaluationOutSchema
from app.schemas.rank import JobPostingSummary
from app.services import rank
from app.services import rank_jobs
from app.db.session import async_session_factory
from app.services.rank import count_jobs_to_rank

router = APIRouter(prefix="/rank", tags=["rank"])


@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_rank(
    payload: RankRequest,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
    locale: str = Depends(get_locale),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Trigger a rank evaluation for unranked jobs.

    Accepts an optional ``Idempotency-Key`` header to prevent duplicate
    submissions (e.g. from network retries).  If a job with that key
    already exists, the existing job is returned instead of creating a new one.
    """
    settings = get_settings()
    window = datetime.now(timezone.utc) - timedelta(seconds=settings.rate_limit_window_seconds)
    count_result = await db.execute(
        select(sa_func.count(ExecutionJob.id)).where(
            ExecutionJob.user_id == user["sub"],
            ExecutionJob.pipeline == "rank",
            ExecutionJob.created_at >= window,
        )
    )
    recent_count = count_result.scalar() or 0
    if recent_count >= settings.rate_limit_attempts:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {settings.rate_limit_attempts} rank runs per {settings.rate_limit_window_seconds // 60} minutes.",
        )

    pdata = payload.model_dump()

    result = await rank_jobs.start(
        async_session_factory,
        user["sub"],
        pdata,
        idempotency_key=idempotency_key,
    )
    return result


@router.get("/status/{job_id}")
async def rank_status(
    job_id: str,
    user: dict = Depends(get_current_user),
    locale: str = Depends(get_locale),
):
    """Get the status of a ranking job. Only returns jobs owned by the authenticated user."""
    result = await rank_jobs.get(job_id, user_id=user["sub"])
    if result is None:
        raise HTTPException(status_code=404, detail=t("errors.not_found", locale))
    return result


@router.post("/cancel/{job_id}")
async def cancel_rank(
    job_id: str,
    user: dict = Depends(get_current_user),
    locale: str = Depends(get_locale),
):
    """Cancel a ranking job. Only cancels if owned by the authenticated user."""
    cancelled = await rank_jobs.cancel(job_id, user_id=user["sub"])
    if not cancelled:
        raise HTTPException(status_code=404, detail=t("errors.not_found", locale))
    return {"cancelled": cancelled}


@router.get("/jobs/count")
async def get_jobs_count(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get count of total jobs to rank and how many are already ranked."""
    return await rank.count_jobs_to_rank(db, user["sub"])


@router.get("/jobs", response_model=list[JobPostingSummary])
async def list_ranked_jobs_endpoint(
    min_score: int | None = Query(None, ge=0, le=100),
    verdict: str | None = Query(None, pattern="^(Strong Fit|Good Fit|Moderate Fit|Weak Fit|Poor Fit)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List ranked jobs with optional filters."""
    return await rank.list_ranked_jobs(
        db=db,
        user_id=user["sub"],
        min_score=min_score,
        verdict=verdict,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}/evaluation", response_model=RankEvaluationOutSchema)
async def get_job_evaluation(
    job_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get the detailed rank evaluation for a specific job."""
    return await rank.get_rank_evaluation(db, job_id, user["sub"])
