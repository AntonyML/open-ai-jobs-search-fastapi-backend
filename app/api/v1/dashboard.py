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


@router.get("/stats")
async def get_dashboard_stats(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated KPIs for the dashboard overview."""
    user_id = user["sub"]

    # ── Jobs scraped (total in DB for this user) ──────────────
    scraped_result = await db.execute(
        select(func.count(JobPosting.id)).where(JobPosting.user_id == user_id)
    )
    jobs_scraped = scraped_result.scalar() or 0

    # ── Jobs ranked (have a rank_score) ──────────────────────
    ranked_result = await db.execute(
        select(func.count(JobPosting.id)).where(
            JobPosting.user_id == user_id,
            JobPosting.rank_score.isnot(None),
        )
    )
    jobs_ranked = ranked_result.scalar() or 0

    # ── Applications created ────────────────────────────────
    apps_result = await db.execute(
        select(func.count(Application.id)).where(Application.user_id == user_id)
    )
    applications = apps_result.scalar() or 0

    # ── Interviews (unique applications with interview prep) ─
    interviews_result = await db.execute(
        select(func.count(InterviewPrep.id)).where(
            InterviewPrep.user_id == user_id
        )
    )
    interviews = interviews_result.scalar() or 0

    # ── Scrape runs (total completed) ────────────────────────
    scrape_runs_result = await db.execute(
        select(func.count(ScrapeRun.id)).where(
            ScrapeRun.user_id == user_id,
            ScrapeRun.status.in_(["completed", "completed_with_errors"]),
        )
    )
    scrape_runs = scrape_runs_result.scalar() or 0

    # ── Avg rank score (of ranked jobs) ──────────────────────
    avg_score_result = await db.execute(
        select(func.avg(JobPosting.rank_score)).where(
            JobPosting.user_id == user_id,
            JobPosting.rank_score.isnot(None),
        )
    )
    avg_rank_score = avg_score_result.scalar()

    # ── Outcomes summary ─────────────────────────────────────
    hired_result = await db.execute(
        select(func.count(Outcome.id)).where(
            Outcome.user_id == user_id, Outcome.status == "hired"
        )
    )
    hired = hired_result.scalar() or 0

    rejected_result = await db.execute(
        select(func.count(Outcome.id)).where(
            Outcome.user_id == user_id, Outcome.status == "rejected"
        )
    )
    rejected = rejected_result.scalar() or 0

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

    # ── Providers configured ──────────────────────────────────
    prov_result = await db.execute(
        select(func.count(ProviderCredential.id)).where(
            ProviderCredential.user_id == user_id
        )
    )
    providers = (prov_result.scalar() or 0) > 0

    # ── Setup completed (profile exists) ──────────────────────
    setup_result = await db.execute(
        select(func.count(CandidateProfile.id)).where(
            CandidateProfile.user_id == user_id
        )
    )
    setup = (setup_result.scalar() or 0) > 0

    # ── Scrape (any jobs in DB) ─────────────────────────────
    scrape_result = await db.execute(
        select(func.count(JobPosting.id)).where(JobPosting.user_id == user_id)
    )
    scrape = (scrape_result.scalar() or 0) > 0

    # ── Rank (any job with rank_score) ───────────────────────
    rank_result = await db.execute(
        select(func.count(JobPosting.id)).where(
            JobPosting.user_id == user_id,
            JobPosting.rank_score.isnot(None),
        )
    )
    rank = (rank_result.scalar() or 0) > 0

    # ── Apply (any application generated) ────────────────────
    apply_result = await db.execute(
        select(func.count(Application.id)).where(Application.user_id == user_id)
    )
    apply = (apply_result.scalar() or 0) > 0

    # ── Interview (any prep generated) ────────────────────────
    interview_result = await db.execute(
        select(func.count(InterviewPrep.id)).where(
            InterviewPrep.user_id == user_id
        )
    )
    interview = (interview_result.scalar() or 0) > 0

    # ── Outcome (any outcome recorded) ────────────────────────
    outcome_result = await db.execute(
        select(func.count(Outcome.id)).where(Outcome.user_id == user_id)
    )
    outcome = (outcome_result.scalar() or 0) > 0

    steps = [
        {"key": "providers", "label": "Providers", "done": providers},
        {"key": "setup", "label": "Setup", "done": setup},
        {"key": "scrape", "label": "Scrape", "done": scrape},
        {"key": "rank", "label": "Rank", "done": rank},
        {"key": "apply", "label": "Apply", "done": apply},
        {"key": "interview", "label": "Interview", "done": interview},
        {"key": "outcome", "label": "Outcome", "done": outcome},
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

    # Count job postings
    total_jobs_result = await db.execute(
        select(func.count(JobPosting.id)).where(JobPosting.user_id == user_id)
    )
    total_jobs = total_jobs_result.scalar() or 0

    # Ranked jobs
    ranked_jobs_result = await db.execute(
        select(func.count(JobPosting.id)).where(
            JobPosting.user_id == user_id,
            JobPosting.rank_score.isnot(None),
        )
    )
    ranked_jobs = ranked_jobs_result.scalar() or 0

    # Applications
    apps_result = await db.execute(
        select(func.count(Application.id)).where(Application.user_id == user_id)
    )
    applications = apps_result.scalar() or 0

    # Interviews
    interviews_result = await db.execute(
        select(func.count(InterviewPrep.id)).where(
            InterviewPrep.user_id == user_id
        )
    )
    interviews = interviews_result.scalar() or 0

    # Hired
    hired_result = await db.execute(
        select(func.count(Outcome.id)).where(
            Outcome.user_id == user_id, Outcome.status == "hired"
        )
    )
    hired = hired_result.scalar() or 0

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
