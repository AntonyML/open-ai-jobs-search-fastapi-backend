"""Outcome router — endpoints for recording job application outcomes."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_max_or_admin
from app.db.models import Outcome
from app.db.session import get_db as _get_db
from app.schemas.outcome import CalibrationReport, OutcomeCreate, OutcomeOut, OutcomeSummaryOut, OutcomeUpdate, TrackerRowOut
from app.services import fit_calibration, outcome
from app.services.tiers import get_tier_limits

router = APIRouter(prefix="/outcome", tags=["outcome"])


@router.post(
    "/",
    response_model=OutcomeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_outcome(
    payload: OutcomeCreate,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Record or update an application outcome.

    Progress updates (application still open):
    - interview_invited, phone_screen_completed, technical_completed,
      case_completed, final_round_completed, offer_received

    Resolutions (application closed):
    - hired, offer_declined, rejected, no_response, interview_only, withdrawn
    """
    tier = user.get("tier", "free")
    max_track = get_tier_limits(tier).get("max_track_count")
    if max_track is not None and tier != "premium":
        result = await db.execute(select(func.count()).where(Outcome.user_id == user["sub"]))
        track_count = result.scalar()
        if track_count >= max_track:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="You have reached the maximum number of tracked outcomes on your current plan. Upgrade to Premium for unlimited tracking.",
            )
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
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List rows from job_search_tracker.csv with pagination."""
    return await outcome.list_tracker_rows(db, user["sub"], limit=limit, offset=offset)


@router.get("/calibration", response_model=CalibrationReport)
async def get_calibration_report(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Generate a calibration report based on all recorded outcomes.

    Analyzes conversion funnel, keyword correlations, and generates
    actionable insights. Requires at least one outcome to be recorded.
    """
    return await fit_calibration.generate_calibration_report(db, user["sub"])