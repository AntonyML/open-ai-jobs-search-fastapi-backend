"""Upskill router — endpoints for skill gap analysis and learning plan generation."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.upskill import (
    UpskillOut,
    UpskillRequest,
    UpskillSummaryOut,
)
from app.services import upskill

router = APIRouter(prefix="/upskill", tags=["upskill"])


@router.post(
    "/",
    response_model=UpskillOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_upskill(
    payload: UpskillRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger an upskill analysis run.

    Runs synchronously in the request (for now). For production, consider
    moving to a background task for long-running analyses.

    Modes:
    - aggregate (default): analyses all ranked jobs for the user
    - targeted: analyses a single job posting (provide target_job_url or target_job_posting_id)
    """
    result = await upskill.execute_upskill(
        db=db,
        user_id=user["sub"],
        mode=payload.mode,
        target_job_url=payload.target_job_url,
        target_job_posting_id=payload.target_job_posting_id,
    )
    return result


@router.get("/{upskill_id}", response_model=UpskillOut)
async def get_upskill(
    upskill_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get an upskill analysis by ID."""
    return await upskill.get_upskill(db, upskill_id, user["sub"])


@router.get("/", response_model=list[UpskillSummaryOut])
async def list_upskills(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all upskill analyses for the authenticated user."""
    return await upskill.list_upskills(db, user["sub"], limit=limit, offset=offset)