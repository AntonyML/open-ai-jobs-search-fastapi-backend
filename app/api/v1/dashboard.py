"""Dashboard API — aggregated stats and analytics for the frontend.

Provides endpoints for:
- /api/v1/dashboard/stats — KPIs for the dashboard overview
- /api/v1/analytics/funnel — conversion funnel data
- /api/v1/analytics/trends — daily activity time series

Each endpoint uses a single aggregated SQL query with scalar subselects
to avoid N+1 round-trips to the database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiocache import Cache, cached
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models import (
    Application,
    GeneratedCV,
    InterviewPrep,
    JobPosting,
    Outcome,
    RankEvaluation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
@cached(ttl=30, cache=Cache.MEMORY, key_builder=lambda f, *a, **kw: f"dash_stats_{kw['user']['sub']}")
async def get_dashboard_stats(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated KPIs — single query with 7 scalar subselects."""
    uid = user["sub"]

    jobs_scraped = select(func.count(JobPosting.id)).where(JobPosting.user_id == uid)
    jobs_ranked = select(func.count(JobPosting.id)).where(JobPosting.user_id == uid, JobPosting.rank_score.isnot(None))
    applications = select(func.count(Application.id)).where(Application.user_id == uid)
    interviews = select(func.count(InterviewPrep.id)).where(InterviewPrep.user_id == uid)
    # Note: scrape_runs tracking was removed — ingesta is now handled by the microservice
    avg_rank_score = select(func.avg(JobPosting.rank_score)).where(
        JobPosting.user_id == uid, JobPosting.rank_score.isnot(None)
    )
    hired = select(func.count(Outcome.id)).where(Outcome.user_id == uid, Outcome.status == "hired")
    rejected = select(func.count(Outcome.id)).where(Outcome.user_id == uid, Outcome.status == "rejected")
    # Document KPIs (the CV builder flow is the core value of the app)
    base_cv_ready = select(func.count(GeneratedCV.id)).where(
        GeneratedCV.user_id == uid,
        GeneratedCV.cv_type == "base",
        GeneratedCV.base_status == "active",
        GeneratedCV.is_deleted.is_(False),
    )
    adapted_cvs = select(func.count(GeneratedCV.id)).where(
        GeneratedCV.user_id == uid,
        GeneratedCV.cv_type == "personalized",
        GeneratedCV.is_deleted.is_(False),
    )
    total_cvs = select(func.count(GeneratedCV.id)).where(
        GeneratedCV.user_id == uid,
        GeneratedCV.is_deleted.is_(False),
    )

    stmt = select(
        func.coalesce(jobs_scraped.scalar_subquery(), 0).label("jobs_scraped"),
        func.coalesce(jobs_ranked.scalar_subquery(), 0).label("jobs_ranked"),
        func.coalesce(applications.scalar_subquery(), 0).label("applications"),
        func.coalesce(interviews.scalar_subquery(), 0).label("interviews"),
        func.coalesce(avg_rank_score.scalar_subquery(), 0).label("avg_rank_score"),
        func.coalesce(hired.scalar_subquery(), 0).label("hired"),
        func.coalesce(rejected.scalar_subquery(), 0).label("rejected"),
        func.coalesce(base_cv_ready.scalar_subquery(), 0).label("base_cv_ready"),
        func.coalesce(adapted_cvs.scalar_subquery(), 0).label("adapted_cv_count"),
        func.coalesce(total_cvs.scalar_subquery(), 0).label("total_cvs"),
    )
    row = (await db.execute(stmt)).one()

    return {
        "jobs_scraped": row.jobs_scraped,
        "jobs_ranked": row.jobs_ranked,
        "applications": row.applications,
        "interviews": row.interviews,
        "avg_rank_score": round(row.avg_rank_score, 1) if row.avg_rank_score is not None else None,
        "hired": row.hired,
        "rejected": row.rejected,
        "base_cv_ready": bool(row.base_cv_ready),
        "adapted_cv_count": row.adapted_cv_count,
        "total_cvs": row.total_cvs,
    }


# ── Analytics router (nested under /analytics) ─────────────────────

analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])


@analytics_router.get("/funnel")
@cached(ttl=60, cache=Cache.MEMORY, key_builder=lambda f, *a, **kw: f"analytics_funnel_{kw['user']['sub']}")
async def get_analytics_funnel(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Conversion funnel — single query with 5 scalar subselects."""
    uid = user["sub"]

    total_jobs = select(func.count(JobPosting.id)).where(JobPosting.user_id == uid)
    ranked_jobs = select(func.count(JobPosting.id)).where(JobPosting.user_id == uid, JobPosting.rank_score.isnot(None))
    applications = select(func.count(Application.id)).where(Application.user_id == uid)
    interviews = select(func.count(InterviewPrep.id)).where(InterviewPrep.user_id == uid)
    hired = select(func.count(Outcome.id)).where(Outcome.user_id == uid, Outcome.status == "hired")

    stmt = select(
        func.coalesce(total_jobs.scalar_subquery(), 0).label("total_jobs"),
        func.coalesce(ranked_jobs.scalar_subquery(), 0).label("ranked_jobs"),
        func.coalesce(applications.scalar_subquery(), 0).label("applications"),
        func.coalesce(interviews.scalar_subquery(), 0).label("interviews"),
        func.coalesce(hired.scalar_subquery(), 0).label("hired"),
    )
    row = (await db.execute(stmt)).one()

    return {
        "funnel": [
            {"stage": "Scraped", "count": row.total_jobs},
            {"stage": "Ranked", "count": row.ranked_jobs},
            {"stage": "Applied", "count": row.applications},
            {"stage": "Interviewed", "count": row.interviews},
            {"stage": "Hired", "count": row.hired},
        ],
        "total_jobs": row.total_jobs,
        "ranked_jobs": row.ranked_jobs,
        "applications": row.applications,
        "interviews": row.interviews,
        "hired": row.hired,
    }


@analytics_router.get("/trends")
@cached(
    ttl=60, cache=Cache.MEMORY, key_builder=lambda f, *a, **kw: f"analytics_trends_{kw['user']['sub']}_{kw['days']}"
)
async def get_analytics_trends(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    days: int = Query(14, ge=7, le=90),
):
    """Daily activity time series for the last N days.

    Returns zero-filled buckets for every calendar day in the window so the
    frontend can render sparklines and bar charts without client-side
    gap-filling.
    """
    uid = user["sub"]
    today = datetime.utcnow().date()
    start = today - timedelta(days=days - 1)

    async def daily_counts(model, extra=None) -> dict[str, int]:
        stmt = select(
            func.date(model.created_at).label("day"),
            func.count().label("n"),
        ).where(
            model.user_id == uid,
            model.created_at >= start,
        )
        if extra is not None:
            stmt = stmt.where(extra)
        rows = (await db.execute(stmt.group_by("day"))).all()
        return {str(r.day): r.n for r in rows}

    scraped = await daily_counts(JobPosting)
    applications = await daily_counts(Application)
    interviews = await daily_counts(InterviewPrep)
    ranked = await daily_counts(RankEvaluation)
    hired = await daily_counts(Outcome, Outcome.status == "hired")

    trends = []
    for offset in range(days):
        key = (start + timedelta(days=offset)).isoformat()
        trends.append(
            {
                "date": key,
                "scraped": scraped.get(key, 0),
                "applications": applications.get(key, 0),
                "interviews": interviews.get(key, 0),
                "ranked": ranked.get(key, 0),
                "hired": hired.get(key, 0),
            }
        )

    return {"days": days, "trends": trends}
