"""Apply router — endpoints for generating tailored CV and cover letter."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.session import get_db as _get_db
from app.schemas.apply import ApplyRequest, ApplyResult
from app.services import apply

router = APIRouter(prefix="/apply", tags=["apply"])


@router.post(
    "/",
    response_model=ApplyResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_apply(
    payload: ApplyRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Generate tailored CV and cover letter for a ranked job.

    Runs synchronously in the request. For production, consider moving
    to a background task for long-running LaTeX compilation.
    """
    result = await apply.execute_apply(
        db=db,
        user_id=user["sub"],
        job_posting_id=payload.job_posting_id,
        rank_evaluation_id=payload.rank_evaluation_id,
        cv_template=payload.cv_template or "moderncv-banking",
        cover_letter_template=payload.cover_letter_template or "cover-cls",
    )
    return result


@router.get("/{application_id}", response_model=apply.ApplicationOut)
async def get_application(
    application_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get a generated application by ID."""
    return await apply.get_application(db, application_id, user["sub"])


@router.get("/", response_model=list[apply.ApplicationOut])
async def list_applications(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all generated applications for the authenticated user."""
    return await apply.list_applications(db, user["sub"], limit=limit, offset=offset)