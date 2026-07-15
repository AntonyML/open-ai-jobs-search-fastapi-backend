"""Apply router — endpoints for generating tailored CV and cover letter."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_llm_provider, get_locale
from app.core.i18n.locale import t
from app.db.models import Application
from app.db.session import get_db as _get_db
from app.schemas.apply import ApplyRequest, ApplyResult, ApplicationOut, ApplicationStatusOut
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
    provider_config: dict = Depends(get_llm_provider),
    locale: str = Depends(get_locale),
):
    """Generate tailored CV and cover letter for a ranked job."""
    result = await apply.execute_apply(
        db=db,
        user_id=user["sub"],
        job_posting_id=payload.job_posting_id,
        rank_evaluation_id=payload.rank_evaluation_id,
        cv_template=payload.cv_template or "moderncv-banking",
        cover_letter_template=payload.cover_letter_template or "cover-cls",
        provider_config=provider_config,
    )
    return result


@router.get("/{application_id}", response_model=ApplicationOut)
async def get_application(
    application_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get a generated application by ID."""
    return await apply.get_application(db, application_id, user["sub"])


@router.get("/{application_id}/status", response_model=ApplicationStatusOut)
async def get_application_status(
    application_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
    locale: str = Depends(get_locale),
):
    """Get the current pipeline stage and progress of an application."""
    result = await db.execute(
        select(Application).where(
            Application.id == application_id,
            Application.user_id == user["sub"],
        )
    )
    app = result.scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail=t("errors.not_found", locale))

    # Map pipeline_stage to progress percentage and action text
    stage_progress = {
        "draft": (10, t("apply.stage.draft", locale)),
        "reviewed": (30, t("apply.stage.reviewed", locale)),
        "revised": (60, t("apply.stage.revised", locale)),
        "compiled": (80, t("apply.stage.compiled", locale)),
        "verified": (100, t("apply.stage.verified", locale)),
    }
    progress_pct, current_action = stage_progress.get(
        app.pipeline_stage, (0, t("apply.stage.initializing", locale))
    )

    return ApplicationStatusOut(
        id=app.id,
        pipeline_stage=app.pipeline_stage,
        progress_pct=progress_pct,
        current_action=current_action,
        review_issues_count=len(app.review_issues) if app.review_issues else 0,
        cv_compiled=app.cv_compiled,
        cover_letter_compiled=app.cover_letter_compiled,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


@router.get("/", response_model=list[ApplicationOut])
async def list_applications(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all generated applications for the authenticated user."""
    return await apply.list_applications(db, user["sub"], limit=limit, offset=offset)