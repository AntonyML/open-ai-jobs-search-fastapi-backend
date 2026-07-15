"""Verification router — POST /apply/{id}/verify to run the FASE 2 checklist.

Runs the comprehensive verification checklist on a generated application's
documents (CV + cover letter + compiled PDF). Stores the result in the
Application.verification_result JSONB column.

The checklist combines:
- 10 deterministic checks (name, email, role, company, dates, braces, placeholders, CID, contact, keywords)
- 1 LLM content quality check (fabricated claims, profile specificity, tone consistency)

Non-blocking — the pipeline always completes regardless of failures.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_llm_provider
from app.db.models import Application, CandidateProfile, JobPosting
from app.db.session import get_db
from app.schemas.verification import VerificationResponse
from app.services import verification

router = APIRouter(tags=["verification"])


@router.post(
    "/apply/{application_id}/verify",
    response_model=VerificationResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_application(
    application_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider_config: dict = Depends(get_llm_provider),
):
    """Run the verification checklist on a generated application's documents.

    Returns the VerificationResult with all 10+ checks, their outcomes,
    and a human-readable summary. The result is persisted in the
    Application record so it can be viewed later without re-running.

    The endpoint is designed to be idempotent: calling it multiple times
    overwrites the previous verification result.

    Responds with 404 if the application is not found or doesn't belong
    to the authenticated user.
    """
    # 1. Load application with ownership check
    result = await db.execute(
        select(Application).where(
            Application.id == application_id,
            Application.user_id == user["sub"],
        )
    )
    app = result.scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")

    # 2. Load job posting
    job_result = await db.execute(
        select(JobPosting).where(
            JobPosting.id == app.job_posting_id,
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job posting not found.")

    # 3. Load candidate profile
    cand_result = await db.execute(
        select(CandidateProfile).where(
            CandidateProfile.user_id == user["sub"],
        )
    )
    candidate = cand_result.scalar_one_or_none()

    # 4. Run verification checklist
    verify_result = await verification.run_verification_checklist(
        application=app,
        candidate=candidate,
        job_posting=job,
        cv_latex=app.draft_cv_tex or "",
        cover_letter_latex=app.draft_cover_letter_tex or "",
        cv_pdf_path=app.cv_pdf_path,
        provider_config=provider_config,
    )

    # 5. Store result in DB
    app.verification_result = verify_result.model_dump()
    await db.commit()

    # 6. Build response
    pass_count = len(verify_result.passes)
    total_count = len(verify_result.checks)
    status_emoji = "✅" if verify_result.overall_pass else "❌"
    message = (
        f"{status_emoji} Verification complete: "
        f"{pass_count}/{total_count} checks passed"
        + (f", {len(verify_result.failures)} issue(s) found." if verify_result.failures else " — all clear!")
    )

    return VerificationResponse(
        application_id=application_id,
        result=verify_result,
        message=message,
    )
