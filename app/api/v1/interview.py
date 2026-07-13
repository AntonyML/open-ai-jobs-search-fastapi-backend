"""Interview router — endpoints for interview preparation."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.session import get_db as _get_db
from app.schemas.interview import InterviewPrepOut, InterviewPrepRequest, InterviewPrepSummaryOut
from app.services import interview

router = APIRouter(prefix="/interview", tags=["interview"])


@router.post(
    "/",
    response_model=InterviewPrepOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_interview_prep(
    payload: InterviewPrepRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Generate interview preparation pack for an application.

    Runs synchronously in the request. For production, consider moving
    to a background task for long-running LLM calls.
    """
    result = await interview.execute_interview_prep(
        db=db,
        user_id=user["sub"],
        application_id=payload.application_id,
        stage=payload.stage,
        interview_date=payload.interview_date,
        interview_format=payload.interview_format,
        interviewer_names=payload.interviewer_names,
    )
    return result


@router.get("/{prep_id}", response_model=InterviewPrepOut)
async def get_interview_prep(
    prep_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get an interview preparation pack by ID."""
    return await interview.get_interview_prep(db, prep_id, user["sub"])


@router.get("/", response_model=list[InterviewPrepSummaryOut])
async def list_interview_preps(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all interview preparation packs for the authenticated user."""
    return await interview.list_interview_preps(db, user["sub"], limit=limit, offset=offset)