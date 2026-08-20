"""Users router — profile, usage, and account related endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import Application, InterviewPrep, JobPosting, Outcome
from app.db.session import get_db as _get_db
from app.services.plans import get_plan

# Fallback limits when the user's plan row is missing from the DB
# (source of truth is the plans catalog — see Plan.limits)
_PAID_DEFAULTS = {
    "max_rank_iterations": 100,
    "max_apply_count": 1000,
    "max_prepare_count": 1000,
    "max_track_count": 1000,
}

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/usage")
async def get_user_usage(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return usage counts and limits for the authenticated user.

    Used by the frontend to proactively disable pipeline buttons
    when the free-tier limit is reached.
    """
    uid = user["sub"]
    tier = user.get("tier", "free")
    plan = await get_plan(db, tier)
    limits = plan.limits if plan else _PAID_DEFAULTS

    apps = select(func.count()).where(Application.user_id == uid)
    preps = select(func.count()).where(InterviewPrep.user_id == uid)
    ranks = select(func.count(JobPosting.id)).where(JobPosting.user_id == uid, JobPosting.rank_score.isnot(None))
    outcomes = select(func.count()).where(Outcome.user_id == uid)

    stmt = select(
        func.coalesce(apps.scalar_subquery(), 0).label("applications"),
        func.coalesce(preps.scalar_subquery(), 0).label("interview_preps"),
        func.coalesce(ranks.scalar_subquery(), 0).label("rank_iterations"),
        func.coalesce(outcomes.scalar_subquery(), 0).label("outcomes"),
    )
    row = (await db.execute(stmt)).one()

    return {
        "tier": tier,
        "limits": {
            "max_apply_count": limits.get("max_apply_count", 1000),
            "max_prepare_count": limits.get("max_prepare_count", 1000),
            "max_rank_iterations": limits.get("max_rank_iterations", 100),
            "max_track_count": limits.get("max_track_count", 1000),
        },
        "usage": {
            "applications": row.applications,
            "interview_preps": row.interview_preps,
            "rank_iterations": row.rank_iterations,
            "outcomes": row.outcomes,
        },
    }
