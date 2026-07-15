"""Pipeline reset router — endpoint for resetting all pipeline tracking data.

DELETE /api/v1/pipeline-reset

This is intentionally a separate router from the /reset endpoint because:
- /reset clears profile/documents (destructive to configuration)
- /pipeline-reset clears pipeline job data (preserves config/providers)

The pipeline-reset endpoint does NOT require a confirmation token because
it only deletes transient job data, not configuration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.services import pipeline_reset

router = APIRouter(prefix="/pipeline-reset", tags=["pipeline-reset"])


@router.delete(
    "/",
    status_code=status.HTTP_200_OK,
)
async def reset_pipeline_data(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all pipeline tracking data for the authenticated user.

    This resets your job search pipeline without affecting your:
    - Provider configuration & API keys
    - Candidate profile & setup data
    - User account & preferences

    Deletes: job postings, evaluations, applications, interview prep,
    outcomes, execution queue history, scrape runs, competency expansions,
    and upskill analyses.
    """
    return await pipeline_reset.execute_pipeline_reset(
        db=db,
        user_id=user["sub"],
    )
