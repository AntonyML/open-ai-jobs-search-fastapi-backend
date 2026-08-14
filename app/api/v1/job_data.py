"""Job data reset router — endpoint for resetting all job search tracking data.

DELETE /api/v1/job-data

This is intentionally a separate router from the /reset endpoint because:
- /reset clears profile/documents (destructive to configuration)
- /job-data clears job search data (preserves config/providers)

The job-data endpoint does NOT require a confirmation token because
it only deletes transient job data, not configuration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.services import job_data

router = APIRouter(prefix="/job-data", tags=["job-data"])


@router.delete(
    "/",
    status_code=status.HTTP_200_OK,
)
async def reset_job_data(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all job search tracking data for the authenticated user.

    This resets your job search data without affecting your:
    - Provider configuration & API keys
    - Candidate profile & setup data
    - User account & preferences

    Deletes: job postings, evaluations, applications, interview prep,
    outcomes, execution queue history, scrape runs, competency expansions,
    and upskill analyses.
    """
    return await job_data.execute_job_data(
        db=db,
        user_id=user["sub"],
    )
