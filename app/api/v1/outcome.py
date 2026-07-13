"""Outcome router — endpoints for recording job application outcomes."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.session import get_db as _get_db
from app.schemas.outcome import OutcomeCreate, OutcomeOut, OutcomeSummaryOut, OutcomeUpdate, TrackerRowOut
from app.services import outcome

router = APIRouter(prefix="/outcome", tags=["outcome"])


@router.post(
    "/",
    response_model=OutcomeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_outcome(
    payload: OutcomeCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Record or update an application outcome.

    Progress updates (application still open):
    - interview_invited, phone_screen_completed, technical_completed,
      case_completed, final_round_completed, offer_received

    Resolutions (application closed):
    - hired, offer_declined, rejected, no_response, interview_only, withdrawn
    """
    return await outcome.execute_outcome(db, user["sub"], payload)


@router.patch("/{outcome_id}", response_model=OutcomeOut)
async def update_outcome(
    outcome_id: str,
    payload: OutcomeUpdate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Update an existing outcome."""
    return await outcome.update_outcome(db, user["sub"], outcome_id, payload)


@router.get("/{outcome_id}", response_model=OutcomeOut)
async def get_outcome(
    outcome_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get an outcome by ID."""
    return await outcome.get_outcome(db, outcome_id, user["sub"])


@router.get("/", response_model=list[OutcomeSummaryOut])
async def list_outcomes(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all outcomes for the authenticated user."""
    return await outcome.list_outcomes(db, user["sub"], limit=limit, offset=offset)


@router.get("/tracker/rows", response_model=list[TrackerRowOut])
async def list_tracker_rows(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all rows from job_search_tracker.csv."""
    return await outcome.list_tracker_rows(db, user["sub"])