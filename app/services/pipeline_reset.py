"""Pipeline reset service — deletes ALL tracked pipeline data for a user.

When a user clicks "Reiniciar pipeline", this service cleans up EVERYTHING
that was generated during the pipeline run:
- JobPostings (cascades: RankEvaluation, Application, InterviewPrep, Outcome)
- ScrapeRuns, ExecutionJobs
- CompetencyExpansions, Upskill analyses
- UserSalaryData (uploaded salary benchmarks)
- Resets: ProviderHealth, ModelHealth, ExecutionQueueState
- job_search_tracker.csv (file on disk)

The following are PRESERVED:
- User account, ProviderCredentials, UserModelSelections
- CandidateProfile, BehavioralProfile, StarExample
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CompetencyExpansion,
    ExecutionJob,
    ExecutionQueueState,
    JobPosting,
    ModelHealth,
    ProviderHealth,
    Upskill,
    User,
    UserSalaryData,
)
from app.exceptions import NotFoundError


async def execute_pipeline_reset(
    db: AsyncSession,
    user_id: str,
) -> dict[str, Any]:
    """Delete ALL pipeline tracking data for the given user.

    This is a COMPLETE reset — not just job postings. It removes all
    tracked data generated during the pipeline run while preserving
    user configuration (providers, profile, behavioral data).

    Args:
        db: Database session.
        user_id: The authenticated user's UUID.

    Returns:
        A summary dict with counts of deleted/reset records.
    """
    # 1. Verify user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError("User not found.")

    deleted: dict[str, int] = {}

    # 2. Delete ExecutionJobs (orchestrator queue history)
    exec_result = await db.execute(
        delete(ExecutionJob).where(ExecutionJob.user_id == user_id)
    )
    deleted["execution_jobs"] = exec_result.rowcount

    # 3. Delete Upskill analyses
    upskill_result = await db.execute(
        delete(Upskill).where(Upskill.user_id == user_id)
    )
    deleted["upskills"] = upskill_result.rowcount

    # 4. Delete CompetencyExpansions
    comp_result = await db.execute(
        delete(CompetencyExpansion).where(CompetencyExpansion.user_id == user_id)
    )
    deleted["competency_expansions"] = comp_result.rowcount

    # 5. Delete JobPostings — CASCADES to:
    #    RankEvaluation, Application, InterviewPrep, Outcome
    #    (all have ondelete="CASCADE" FK to job_postings or their children)
    job_result = await db.execute(
        delete(JobPosting).where(JobPosting.user_id == user_id)
    )
    deleted["job_postings"] = job_result.rowcount

    # 6. (Removed: ScrapeRun table was deprecated with the migration to ingesta microservice)

    # 7. Delete UserSalaryData (uploaded salary benchmarks)
    salary_result = await db.execute(
        delete(UserSalaryData).where(UserSalaryData.user_id == user_id)
    )
    deleted["salary_data"] = salary_result.rowcount

    # 8. Reset ProviderHealth metrics (keep the rows, zero the counters)
    health_result = await db.execute(
        update(ProviderHealth)
        .where(ProviderHealth.user_id == user_id)
        .values(
            status="healthy",
            cooldown_until=None,
            last_latency_ms=None,
            last_error=None,
            last_error_code=None,
            total_calls=0,
            success_count=0,
            failure_count=0,
            rate_limit_count=0,
            timeout_count=0,
            consecutive_failures=0,
            health_score=1.0,
        )
    )
    deleted["provider_health_reset"] = health_result.rowcount

    # 9. Reset ModelHealth metrics
    model_result = await db.execute(
        update(ModelHealth)
        .where(ModelHealth.user_id == user_id)
        .values(
            state="READY",
            cooldown_until=None,
            average_latency_ms=None,
            average_success_rate=1.0,
            total_calls=0,
            last_error=None,
            last_error_code=None,
        )
    )
    deleted["model_health_reset"] = model_result.rowcount

    # 10. Reset ExecutionQueueState
    queue_result = await db.execute(
        update(ExecutionQueueState)
        .where(ExecutionQueueState.user_id == user_id)
        .values(
            paused=False,
            active_workers=0,
            total_enqueued=0,
            total_completed=0,
            total_failed=0,
            total_cancelled=0,
        )
    )
    deleted["queue_state_reset"] = queue_result.rowcount

    # 11. Delete job_search_tracker.csv (if it exists)
    try:
        from app.core.settings import get_settings
        tracker_path = Path(get_settings().tracker_path)
        if tracker_path.exists():
            tracker_path.unlink()
            deleted["tracker_csv"] = 1
    except Exception:
        pass  # Non-critical — file might not be configured

    # 12. Commit all changes
    await db.commit()

    # 13. Build summary
    total = sum(deleted.values())
    detail_parts = []
    for kind, count in deleted.items():
        if count == 0:
            continue
        if kind.endswith("_reset"):
            # Health/queue resets: "Provider health → 3 rows reset"
            label = kind.replace("_reset", "").replace("_", " ").title()
            detail_parts.append(f"{label}: {count} row(s) reset")
        else:
            label = kind.replace("_", " ").title()
            detail_parts.append(f"{label}: {count}")

    return {
        "status": "success",
        "deleted": deleted,
        "total_deleted": total,
        "message": (
            f"Pipeline reset complete. "
            f"{', '.join(detail_parts)}. "
            "Your providers, profile, and settings are unchanged."
        ),
    }
