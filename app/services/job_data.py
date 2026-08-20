"""Job data reset service — deletes ALL tracked job search data for a user.

When a user clicks "Reiniciar búsqueda de empleo", this service cleans up EVERYTHING
that was generated during the job search run:
- JobPostings (cascades: RankEvaluation, Application, InterviewPrep, Outcome)
- ScrapeRuns, ExecutionJobs
- CompetencyExpansions, Upskill analyses
- UserSalaryData (uploaded salary benchmarks)
- On-disk artifacts under ``{generated_storage_path}/{user_id}/`` (tailored CV +
  cover letter PDFs from the apply pipeline), purged via ``artifact_store``
- Resets: ExecutionQueueState
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
    Upskill,
    User,
    UserSalaryData,
)
from app.exceptions import NotFoundError
from app.services import artifact_store


async def execute_job_data(
    db: AsyncSession,
    user_id: str,
) -> dict[str, Any]:
    """Delete ALL job search tracking data for the given user.

    This is a COMPLETE reset — not just job postings. It removes all
    tracked data generated during the job search run while preserving
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
    exec_result = await db.execute(delete(ExecutionJob).where(ExecutionJob.user_id == user_id))
    deleted["execution_jobs"] = exec_result.rowcount

    # 3. Delete Upskill analyses
    upskill_result = await db.execute(delete(Upskill).where(Upskill.user_id == user_id))
    deleted["upskills"] = upskill_result.rowcount

    # 4. Delete CompetencyExpansions
    comp_result = await db.execute(delete(CompetencyExpansion).where(CompetencyExpansion.user_id == user_id))
    deleted["competency_expansions"] = comp_result.rowcount

    # 5. Delete JobPostings — CASCADES to:
    #    RankEvaluation, Application, InterviewPrep, Outcome
    #    (all have ondelete="CASCADE" FK to job_postings or their children)
    job_result = await db.execute(delete(JobPosting).where(JobPosting.user_id == user_id))
    deleted["job_postings"] = job_result.rowcount

    # 6. (Removed: ScrapeRun table was deprecated with the migration to ingesta microservice)

    # 7. Delete UserSalaryData (uploaded salary benchmarks)
    salary_result = await db.execute(delete(UserSalaryData).where(UserSalaryData.user_id == user_id))
    deleted["salary_data"] = salary_result.rowcount

    # 8. Reset ExecutionQueueState
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

    # 9. Delete job_search_tracker.csv (if it exists)
    try:
        from app.core.settings import get_settings

        tracker_path = Path(get_settings().tracker_path)
        if tracker_path.exists():
            tracker_path.unlink()
            deleted["tracker_csv"] = 1
    except Exception:
        pass  # Non-critical — file might not be configured

    # 10. Purge on-disk apply artifacts for this user (tailored CV + cover
    #     letter PDFs under ``{generated_storage_path}/{user_id}/``). The rows
    #     were removed by the JobPosting cascade above; without this step the
    #     files would be orphaned.
    try:
        if artifact_store.remove_user_dir("apply", user_id):
            deleted["apply_artifacts_purged"] = 1
    except Exception:
        pass  # Non-critical — storage may not be configured

    # 11. Commit all changes
    await db.commit()

    # 12. Build summary
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
            f"Job data reset complete. {', '.join(detail_parts)}. Your providers, profile, and settings are unchanged."
        ),
    }
