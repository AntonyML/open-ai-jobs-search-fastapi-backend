"""Rank router — endpoints for ranking job postings."""

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_locale
from app.core.i18n.locale import t
from app.db.session import get_db as _get_db
from app.schemas.rank import RankRequest, RankResult
from app.schemas.rank import RankEvaluationOut as RankEvaluationOutSchema
from app.schemas.scrape import JobPostingSummary
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
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
    locale: str = Depends(get_locale),
):
    """Trigger a rank evaluation for unranked jobs."""
    job_id = await rank_jobs.start(async_session_factory, user["sub"], payload.model_dump())
    counts = await count_jobs_to_rank(db, user["sub"], payload.model_dump())
    return {"job_id": job_id, "status": "running", "total_jobs": counts["total"]}

@router.get("/status/{job_id}")
async def rank_status(
    job_id: str,
    locale: str = Depends(get_locale),
):
    result = await rank_jobs.get(job_id)
    return result or {"detail": t("errors.not_found", locale)}

@router.post("/cancel/{job_id}")
async def cancel_rank(job_id: str):
    cancelled = await rank_jobs.cancel(job_id)
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
