"""Pipeline reset service — deletes all tracked pipeline data for a user.

When a user clicks "Reiniciar pipeline", this service cleans up:
- JobPostings (and cascading: RankEvaluation, Application, InterviewPrep, Outcome)
- ScrapeRuns
- ExecutionJobs (orchestrator queue jobs)
- CompetencyExpansions
- Upskill analyses

The following are PRESERVED so the user doesn't have to reconfigure:
- User account, ProviderCredentials, UserModelSelections
- CandidateProfile (setup/profile data)
- BehavioralProfile, StarExample
- ProviderHealth, ModelHealth, ExecutionQueueState
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Application,
    CompetencyExpansion,
    ExecutionJob,
    InterviewPrep,
    JobPosting,
    Outcome,
    RankEvaluation,
    ScrapeRun,
    Upskill,
    User,
)
from app.exceptions import NotFoundError


async def execute_pipeline_reset(
    db: AsyncSession,
    user_id: str,
) -> dict[str, Any]:
    """Delete ALL pipeline tracking data for the given user.

    Args:
        db: Database session.
        user_id: The authenticated user's UUID.

    Returns:
        A summary dict with counts of deleted records.
    """
    # 1. Verify user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError("User not found.")

    deleted: dict[str, int] = {}

    # 2. Delete ExecutionJobs (orchestrator queue)
    exec_result = await db.execute(
        delete(ExecutionJob).where(ExecutionJob.user_id == user_id)
    )
    deleted["execution_jobs"] = exec_result.rowcount

    # 3. Delete Upskill analyses & CompetencyExpansions
    #    These reference candidate_profile via candidate_id, so we need
    #    to find the profile first, then delete by candidate_id.
    #    But they also have user_id, so delete by user_id is safe.
    upskill_result = await db.execute(
        delete(Upskill).where(Upskill.user_id == user_id)
    )
    deleted["upskills"] = upskill_result.rowcount

    comp_result = await db.execute(
        delete(CompetencyExpansion).where(CompetencyExpansion.user_id == user_id)
    )
    deleted["competency_expansions"] = comp_result.rowcount

    # 4. Delete JobPostings — this CASCADES to:
    #    RankEvaluation, Application, InterviewPrep, Outcome
    #    because of ondelete="CASCADE" in the FK constraints.
    job_result = await db.execute(
        delete(JobPosting).where(JobPosting.user_id == user_id)
    )
    deleted["job_postings"] = job_result.rowcount
    # NOTE: The cascaded deletes are handled by PostgreSQL automatically.
    # We still report them separately by checking remaining rows.
    # But since we just deleted the parent rows, the children are gone.
    # We can give a rough estimate from the cascade.

    # 5. Delete ScrapeRuns
    scrape_result = await db.execute(
        delete(ScrapeRun).where(ScrapeRun.user_id == user_id)
    )
    deleted["scrape_runs"] = scrape_result.rowcount

    # 6. Commit all deletions
    await db.commit()

    # 7. Build summary
    total = sum(deleted.values())
    detail_parts = []
    for kind, count in deleted.items():
        label = kind.replace("_", " ").title()
        detail_parts.append(f"{label}: {count}")

    return {
        "status": "success",
        "deleted": deleted,
        "total_deleted": total,
        "message": (
            f"Pipeline reset complete. Deleted {total} record(s): "
            f"{', '.join(detail_parts)}. "
            "Your providers, profile, and settings are unchanged."
        ),
    }
