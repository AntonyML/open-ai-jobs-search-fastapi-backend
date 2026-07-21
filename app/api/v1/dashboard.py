"""Dashboard API — aggregated stats and analytics for the frontend.

Provides endpoints for:
- /api/v1/dashboard/stats — KPIs for the dashboard overview
- /api/v1/dashboard/pipeline — pipeline progress per step
- /api/v1/analytics/funnel — conversion funnel data

Each endpoint uses a single aggregated SQL query with scalar subselects
to avoid N+1 round-trips to the database.
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


@router.get("/stats")
async def get_dashboard_stats(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated KPIs — single query with 8 scalar subselects."""
    uid = user["sub"]

    jobs_scraped = select(func.count(JobPosting.id)).where(JobPosting.user_id == uid)
    jobs_ranked = select(func.count(JobPosting.id)).where(
        JobPosting.user_id == uid, JobPosting.rank_score.isnot(None)
    )
    applications = select(func.count(Application.id)).where(Application.user_id == uid)
    interviews = select(func.count(InterviewPrep.id)).where(InterviewPrep.user_id == uid)
    scrape_runs = select(func.count(ScrapeRun.id)).where(
        ScrapeRun.user_id == uid,
        ScrapeRun.status.in_(["completed", "completed_with_errors"]),
    )
    avg_rank_score = select(func.avg(JobPosting.rank_score)).where(
        JobPosting.user_id == uid, JobPosting.rank_score.isnot(None)
    )
    hired = select(func.count(Outcome.id)).where(
        Outcome.user_id == uid, Outcome.status == "hired"
    )
    rejected = select(func.count(Outcome.id)).where(
        Outcome.user_id == uid, Outcome.status == "rejected"
    )

    stmt = select(
        func.coalesce(jobs_scraped.scalar_subquery(), 0).label("jobs_scraped"),
        func.coalesce(jobs_ranked.scalar_subquery(), 0).label("jobs_ranked"),
        func.coalesce(applications.scalar_subquery(), 0).label("applications"),
        func.coalesce(interviews.scalar_subquery(), 0).label("interviews"),
        func.coalesce(scrape_runs.scalar_subquery(), 0).label("scrape_runs"),
        avg_rank_score.scalar_subquery().label("avg_rank_score"),
        func.coalesce(hired.scalar_subquery(), 0).label("hired"),
        func.coalesce(rejected.scalar_subquery(), 0).label("rejected"),
    )
    row = (await db.execute(stmt)).one()

    return {
        "jobs_scraped": row.jobs_scraped,
        "jobs_ranked": row.jobs_ranked,
        "applications": row.applications,
        "interviews": row.interviews,
        "scrape_runs": row.scrape_runs,
        "avg_rank_score": round(row.avg_rank_score, 1) if row.avg_rank_score is not None else None,
        "hired": row.hired,
        "rejected": row.rejected,
    }


@router.get("/pipeline")
async def get_pipeline_progress(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Pipeline progress — single query with 7 scalar subselects."""
    uid = user["sub"]

    providers = select(func.count(ProviderCredential.id)).where(ProviderCredential.user_id == uid)
    setup = select(func.count(CandidateProfile.id)).where(CandidateProfile.user_id == uid)
    scrape = select(func.count(JobPosting.id)).where(JobPosting.user_id == uid)
    rank = select(func.count(JobPosting.id)).where(
        JobPosting.user_id == uid, JobPosting.rank_score.isnot(None)
    )
    apply = select(func.count(Application.id)).where(Application.user_id == uid)
    interview = select(func.count(InterviewPrep.id)).where(InterviewPrep.user_id == uid)
    outcome = select(func.count(Outcome.id)).where(Outcome.user_id == uid)

    stmt = select(
        func.coalesce(providers.scalar_subquery(), 0).label("providers"),
        func.coalesce(setup.scalar_subquery(), 0).label("setup"),
        func.coalesce(scrape.scalar_subquery(), 0).label("scrape"),
        func.coalesce(rank.scalar_subquery(), 0).label("rank"),
        func.coalesce(apply.scalar_subquery(), 0).label("apply"),
        func.coalesce(interview.scalar_subquery(), 0).label("interview"),
        func.coalesce(outcome.scalar_subquery(), 0).label("outcome"),
    )
    row = (await db.execute(stmt)).one()

    steps = [
        {"key": "providers", "label": "Providers", "done": row.providers > 0},
        {"key": "setup", "label": "Setup", "done": row.setup > 0},
        {"key": "scrape", "label": "Scrape", "done": row.scrape > 0},
        {"key": "rank", "label": "Rank", "done": row.rank > 0},
        {"key": "apply", "label": "Apply", "done": row.apply > 0},
        {"key": "interview", "label": "Interview", "done": row.interview > 0},
        {"key": "outcome", "label": "Outcome", "done": row.outcome > 0},
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
    """Conversion funnel — single query with 5 scalar subselects."""
    uid = user["sub"]

    total_jobs = select(func.count(JobPosting.id)).where(JobPosting.user_id == uid)
    ranked_jobs = select(func.count(JobPosting.id)).where(
        JobPosting.user_id == uid, JobPosting.rank_score.isnot(None)
    )
    applications = select(func.count(Application.id)).where(Application.user_id == uid)
    interviews = select(func.count(InterviewPrep.id)).where(InterviewPrep.user_id == uid)
    hired = select(func.count(Outcome.id)).where(
        Outcome.user_id == uid, Outcome.status == "hired"
    )

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
