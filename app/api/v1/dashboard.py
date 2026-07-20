"""Dashboard API — aggregated stats and analytics for the frontend.

Provides endpoints for:
- /api/v1/dashboard/stats — KPIs for the dashboard overview
- /api/v1/dashboard/pipeline — pipeline progress per step
- /api/v1/analytics/funnel — conversion funnel data
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models import (
    Application,
    CandidateProfile,
    InterviewPrep,
    JobPosting,
    Outcome,
    ProviderCredential,
    ScrapeRun,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _count(db: AsyncSession, model, user_id: str, *extra_filters):
    stmt = select(func.count(model.id)).where(model.user_id == user_id)
    if extra_filters:
        stmt = stmt.where(*extra_filters)
    result = await db.execute(stmt)
    return result.scalar() or 0


async def _avg(db: AsyncSession, model, column, user_id: str, *extra_filters):
    stmt = select(func.avg(column)).where(model.user_id == user_id)
    if extra_filters:
        stmt = stmt.where(*extra_filters)
    result = await db.execute(stmt)
    return result.scalar()


@router.get("/stats")
async def get_dashboard_stats(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated KPIs for the dashboard overview."""
    user_id = user["sub"]

    jobs_scraped = await _count(db, JobPosting, user_id)
    jobs_ranked = await _count(db, JobPosting, user_id, JobPosting.rank_score.isnot(None))
    applications = await _count(db, Application, user_id)
    interviews = await _count(db, InterviewPrep, user_id)
    scrape_runs = await _count(db, ScrapeRun, user_id, ScrapeRun.status.in_(["completed", "completed_with_errors"]))
    avg_rank_score = await _avg(db, JobPosting, JobPosting.rank_score, user_id, JobPosting.rank_score.isnot(None))
    hired = await _count(db, Outcome, user_id, Outcome.status == "hired")
    rejected = await _count(db, Outcome, user_id, Outcome.status == "rejected")

    return {
        "jobs_scraped": jobs_scraped,
        "jobs_ranked": jobs_ranked,
        "applications": applications,
        "interviews": interviews,
        "scrape_runs": scrape_runs,
        "avg_rank_score": round(avg_rank_score, 1) if avg_rank_score else None,
        "hired": hired,
        "rejected": rejected,
    }


@router.get("/pipeline")
async def get_pipeline_progress(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check which pipeline steps have data for this user."""
    user_id = user["sub"]

    providers = await _count(db, ProviderCredential, user_id)
    setup = await _count(db, CandidateProfile, user_id)
    scrape = await _count(db, JobPosting, user_id)
    rank = await _count(db, JobPosting, user_id, JobPosting.rank_score.isnot(None))
    apply = await _count(db, Application, user_id)
    interview = await _count(db, InterviewPrep, user_id)
    outcome = await _count(db, Outcome, user_id)

    steps = [
        {"key": "providers", "label": "Providers", "done": providers > 0},
        {"key": "setup", "label": "Setup", "done": setup > 0},
        {"key": "scrape", "label": "Scrape", "done": scrape > 0},
        {"key": "rank", "label": "Rank", "done": rank > 0},
        {"key": "apply", "label": "Apply", "done": apply > 0},
        {"key": "interview", "label": "Interview", "done": interview > 0},
        {"key": "outcome", "label": "Outcome", "done": outcome > 0},
    ]

    completed = sum(1 for s in steps if s["done"])

    return {"steps": steps, "completed": completed, "total": len(steps)}


# ── Analytics router (nested under /analytics) ─────────────────────

analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])


@analytics_router.get("/funnel")
async def get_analytics_funnel(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get conversion funnel data for analytics charts."""
    user_id = user["sub"]

    total_jobs = await _count(db, JobPosting, user_id)
    ranked_jobs = await _count(db, JobPosting, user_id, JobPosting.rank_score.isnot(None))
    applications = await _count(db, Application, user_id)
    interviews = await _count(db, InterviewPrep, user_id)
    hired = await _count(db, Outcome, user_id, Outcome.status == "hired")

    return {
        "funnel": [
            {"stage": "Scraped", "count": total_jobs},
            {"stage": "Ranked", "count": ranked_jobs},
            {"stage": "Applied", "count": applications},
            {"stage": "Interviewed", "count": interviews},
            {"stage": "Hired", "count": hired},
        ],
        "total_jobs": total_jobs,
        "ranked_jobs": ranked_jobs,
        "applications": applications,
        "interviews": interviews,
        "hired": hired,
    }
