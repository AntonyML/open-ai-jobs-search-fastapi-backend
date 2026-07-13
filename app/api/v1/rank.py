"""Rank router — endpoints for ranking job postings."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.session import get_db as _get_db
from app.schemas.rank import RankRequest, RankResult
from app.schemas.rank import RankEvaluationOut as RankEvaluationOutSchema
from app.schemas.scrape import JobPostingSummary
from app.services import rank

router = APIRouter(prefix="/rank", tags=["rank"])


@router.post(
    "/",
    response_model=RankResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_rank(
    payload: RankRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Trigger a rank evaluation for unranked jobs.

    Runs synchronously in the request. For large batches, consider
    moving to a background task in a future iteration.
    """
    result = await rank.execute_rank(
        db=db,
        user_id=user["sub"],
        focus_area=payload.focus_area,
        re_rank=payload.re_rank,
        top_n=payload.top_n,
    )
    return result


@router.get("/jobs", response_model=list[JobPostingSummary])
async def list_ranked_jobs(
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