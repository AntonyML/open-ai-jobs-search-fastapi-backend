"""Upskill router — endpoints for skill gap analysis and learning plan generation."""

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_locale
from app.core.i18n.locale import t
from app.db.models import CandidateProfile, Upskill
from app.db.session import get_db as _get_db
from app.exceptions import ProfileIncompleteError
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
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Trigger an upskill analysis run.

    Runs as a background task. Returns immediately with the upskill record (status=pending).
    Poll GET /upskill/{upskill_id} to check completion.

    Modes:
    - aggregate (default): analyses all ranked jobs for the user
    - targeted: analyses a single job posting (provide target_job_url or target_job_posting_id)
    """
    # 1. Get candidate profile
    candidate_result = await db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user["sub"])
    )
    candidate = candidate_result.scalar_one_or_none()
    if candidate is None:
        raise ProfileIncompleteError("Candidate profile not found. Run /setup first.")

    # 2. Create upskill record with pending status
    upskill_record = Upskill(
        user_id=user["sub"],
        candidate_id=candidate.id,
        mode=payload.mode,
        target_job_posting_id=payload.target_job_posting_id,
        target_job_url=payload.target_job_url,
        status="pending",
    )
    db.add(upskill_record)
    await db.commit()
    await db.refresh(upskill_record)

    # 3. Add background task
    background_tasks.add_task(upskill._execute_upskill_background, upskill_record.id)

    return upskill_record


@router.get("/{upskill_id}", response_model=UpskillOut)
async def get_upskill(
    upskill_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get an upskill analysis by ID."""
    return await upskill.get_upskill(db, upskill_id, user["sub"])


@router.get("/", response_model=list[UpskillSummaryOut])
async def list_upskills(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all upskill analyses for the authenticated user.

    Errors are silently handled — returns empty list instead of 500.
    """
    try:
        return await upskill.list_upskills(db, user["sub"], limit=limit, offset=offset)
    except Exception:
        return []