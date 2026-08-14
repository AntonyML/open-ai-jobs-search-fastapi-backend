"""Apply router — endpoints for generating tailored CV and cover letter."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_llm_provider, get_locale, require_max_or_admin
from app.core.i18n.locale import t
from app.db.models import Application, JobPosting, RankEvaluation
from app.db.session import get_db as _get_db
from app.schemas.apply import ApplyRequest, ApplyResult, ApplicationOut, ApplicationStatusOut
from app.schemas.rank import JobPostingSummary
from app.services import apply
from app.services.tiers import get_tier_limits

router = APIRouter(prefix="/apply", tags=["apply"])


@router.post(
    "/",
    response_model=ApplyResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_apply(
    payload: ApplyRequest,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
    provider_config: dict = Depends(get_llm_provider),
    locale: str = Depends(get_locale),
):
    """Generate tailored CV and cover letter for a ranked job.

    Creates the Application record immediately and schedules the
    pipeline via a background task.  Poll ``GET /{id}/status`` for
    real-time progress.
    """
    tier = user.get("tier", "free")
    max_apply = get_tier_limits(tier).get("max_apply_count")
    if max_apply is not None and tier != "premium":
        result = await db.execute(select(func.count()).where(Application.user_id == user["sub"]))
        app_count = result.scalar()
        if app_count >= max_apply:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="You have reached the maximum number of applications on your current plan. Upgrade to Premium for unlimited applications.",
            )

    # Resolve rank evaluation and validate job ownership
    job_fut = db.execute(
        select(JobPosting).where(
            JobPosting.id == payload.job_posting_id,
            JobPosting.user_id == user["sub"],
        )
    )
    if payload.rank_evaluation_id:
        eval_fut = db.execute(
            select(RankEvaluation).where(
                RankEvaluation.id == payload.rank_evaluation_id,
                RankEvaluation.job_posting_id == payload.job_posting_id,
            )
        )
    else:
        eval_fut = db.execute(
            select(RankEvaluation)
            .where(RankEvaluation.job_posting_id == payload.job_posting_id)
            .order_by(RankEvaluation.created_at.desc())
        )

    job_res, eval_res = await asyncio.gather(job_fut, eval_fut)
    job = job_res.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=t("errors.not_found", locale))
    evaluation = eval_res.scalar_one_or_none()
    if evaluation is None:
        raise HTTPException(
            status_code=404,
            detail=t("errors.not_found", locale) + " — Run /rank first.",
        )

    # Delete any previous failed / stuck application for this job
    clean_old = await db.execute(
        select(Application).where(
            Application.user_id == user["sub"],
            Application.job_posting_id == payload.job_posting_id,
            Application.stage.notin_({"compiled", "verified"}),
        )
    )
    for stale in clean_old.scalars().all():
        await db.delete(stale)
    await db.commit()

    # Create Application record immediately for status tracking
    application = Application(
        user_id=user["sub"],
        job_posting_id=payload.job_posting_id,
        rank_evaluation_id=evaluation.id,
        stage="queued",
        cv_template=payload.cv_template or "moderncv-banking",
        cover_letter_template=payload.cover_letter_template or "cover-cls",
        language=job.language or "en",
    )
    db.add(application)
    await db.commit()
    await db.refresh(application)

    # Schedule background pipeline — returns immediately
    asyncio.create_task(
        apply.execute_apply_background(
            application_id=application.id,
            provider_config=provider_config,
        )
    )

    return ApplyResult(
        application_id=application.id,
        cv_compiled=False,
        cv_pages=None,
        cover_letter_compiled=False,
        cover_letter_pages=None,
        message="Application queued. Check status endpoint for progress.",
    )


@router.get("/available-jobs", response_model=list[JobPostingSummary])
async def list_available_jobs(
    limit: int = 200,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List ranked jobs available to apply.

    Includes jobs with no application yet, AND jobs where the most recent
    application failed — allowing the user to retry.
    """
    terminal_stages = {"compiled", "verified", "draft", "reviewed", "revised"}
    active_application_exists = exists().where(
        Application.user_id == user["sub"],
        Application.job_posting_id == JobPosting.id,
        Application.stage.in_(terminal_stages),
    )
    result = await db.execute(
        select(JobPosting)
        .where(
            JobPosting.user_id == user["sub"],
            JobPosting.status == "ranked",
            ~active_application_exists,
        )
        .order_by(JobPosting.rank_score.desc().nullslast())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


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

    # Map stage to progress percentage and action text
    stage_progress = {
        "queued": (0, t("apply.stage.queued", locale)),
        "initializing": (3, t("apply.stage.initializing", locale)),
        "draft": (10, t("apply.stage.draft", locale)),
        "reviewed": (30, t("apply.stage.reviewed", locale)),
        "revised": (60, t("apply.stage.revised", locale)),
        "compiled": (80, t("apply.stage.compiled", locale)),
        "verified": (100, t("apply.stage.verified", locale)),
        "failed": (0, t("apply.stage.failed", locale)),
    }
    progress_pct, current_action = stage_progress.get(
        app.stage, (0, t("apply.stage.initializing", locale))
    )

    return ApplicationStatusOut(
        id=app.id,
        stage=app.stage,
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
