"""Users router — profile, usage, and account related endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models import Application, InterviewPrep, JobPosting, Outcome
from app.db.session import get_db as _get_db
from app.services.tiers import get_tier_limits

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
    user_id = user["sub"]
    tier = user.get("tier", "free")
    limits = get_tier_limits(tier)

    # ── Application count ───────────────────────────────────────
    app_result = await db.execute(
        select(func.count()).where(Application.user_id == user_id)
    )
    app_count = app_result.scalar() or 0

    # ── Interview prep count ────────────────────────────────────
    prep_result = await db.execute(
        select(func.count()).where(InterviewPrep.user_id == user_id)
    )
    prep_count = prep_result.scalar() or 0

    # ── Rank iterations (count distinct rank_score updates) ─────
    rank_result = await db.execute(
        select(func.count(JobPosting.id)).where(
            JobPosting.user_id == user_id,
            JobPosting.rank_score.isnot(None),
        )
    )
    rank_count = rank_result.scalar() or 0

    # ── Outcome count ───────────────────────────────────────────
    outcome_result = await db.execute(
        select(func.count()).where(Outcome.user_id == user_id)
    )
    outcome_count = outcome_result.scalar() or 0

    return {
        "tier": tier,
        "limits": {
            "max_apply_count": limits.get("max_apply_count", 1000),
            "max_prepare_count": limits.get("max_prepare_count", 1000),
            "max_rank_iterations": limits.get("max_rank_iterations", 100),
            "max_track_count": limits.get("max_track_count", 1000),
        },
        "usage": {
            "applications": app_count,
            "interview_preps": prep_count,
            "rank_iterations": rank_count,
            "outcomes": outcome_count,
        },
    }
