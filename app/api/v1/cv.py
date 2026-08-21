"""CV generator router — generate, list, download and soft-delete CVs.

Endpoints:

- ``POST   /cv/base``          → generic base CV (no job context)
- ``POST   /cv/personalize``   → CV tailored to a free-text job description
- ``GET    /cv/``              → list the user's CVs
- ``GET    /cv/{id}``          → fetch one CV (JSON + analysis)
- ``GET    /cv/{id}/download`` → download the compiled PDF
- ``DELETE /cv/{id}``          → soft-delete a CV
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models import GeneratedCV
from app.schemas.cv import (
    CVAdaptUrlCreate,
    CVAnalysis,
    CVBaseCreate,
    CVJobOut,
    CVPersonalizeCreate,
    CVPersonalizeJobCreate,
    CVRecoverCreate,
    CVResponse,
)
from app.services import credits, cv_generator, r2_storage
from app.services.access_gate import enforce_action_gate
from app.services.cv_generator import (
    build_pdf_url,
    resolve_pdf_path,
)

router = APIRouter(prefix="/cv", tags=["cv"])


def _to_response(record: GeneratedCV) -> CVResponse:
    """Convert a GeneratedCV ORM row to the API response schema."""
    analysis = None
    if record.analysis:
        try:
            analysis = CVAnalysis(**record.analysis)
        except Exception:
            analysis = None
    job = None
    if record.job_posting is not None:
        jp = record.job_posting
        job = CVJobOut(
            id=jp.id,
            title=jp.title,
            company=jp.company,
            location=jp.location,
        )
    return CVResponse(
        cv_id=record.id,
        cv_type=record.cv_type,  # type: ignore[arg-type]
        base_status=record.base_status,  # type: ignore[arg-type]
        job_url=record.job_url,
        job_posting_id=record.job_posting_id,
        job=job,
        job_description_text=record.job_description_text,
        json_cv=record.cv_json or {},
        pdf_url=build_pdf_url(record),
        pdf_ready=record.pdf_path is not None,
        analysis=analysis,
        created_at=record.created_at,
    )


async def _record_usage_after(
    db: AsyncSession,
    correlation_id: str | None,
    usage: dict[str, Any],
) -> None:
    """Attach real token/cost usage to the ledger row, when one was created."""
    if not correlation_id:
        return
    await credits.record_llm_usage(
        db,
        correlation_id,
        model_used=usage.get("model_used"),
        tokens_input=usage.get("tokens_input", 0),
        tokens_output=usage.get("tokens_output", 0),
        cost_usd_cents=usage.get("cost_usd_cents", 0),
    )


@router.post("/base", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_base_cv(
    payload: CVBaseCreate,
    background_tasks: BackgroundTasks,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a generic base CV from the candidate profile.

    The response returns as soon as the JSON is validated; the PDF compiles in
    the background and the row's ``pdf_ready`` flips to true when done.
    """
    usage: dict[str, Any] = {"tokens_input": 0, "tokens_output": 0, "cost_usd_cents": 0, "model_used": None}
    cid = await enforce_action_gate(db, user, "cv_base", label="Base CV generation")
    record = await cv_generator.generate_base_cv(db, user["sub"], usage=usage)
    background_tasks.add_task(cv_generator.compile_cv_in_background, record.id, user["sub"], record.cv_json)
    await _record_usage_after(db, cid, usage)
    return _to_response(record)


@router.post("/base/recover", response_model=CVResponse)
async def recover_base_cv(
    payload: CVRecoverCreate,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore a previous (obsolete) base CV as the active one.

    Free operation (no LLM call): the restored CV becomes ``active`` and the
    current active base CV is demoted to ``obsolete``. The max-2 invariant
    is preserved — never a third document.
    """
    record = await cv_generator.recover_previous_base(db, user["sub"], payload.cv_id)
    return _to_response(record)


@router.post("/personalize", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_personalized_cv(
    payload: CVPersonalizeCreate,
    background_tasks: BackgroundTasks,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tailor a CV to a free-text job description (no scraping, no URL)."""
    usage: dict[str, Any] = {"tokens_input": 0, "tokens_output": 0, "cost_usd_cents": 0, "model_used": None}
    cid = await enforce_action_gate(db, user, "cv_base", label="Personalized CV generation")
    record = await cv_generator.personalize_cv(
        db,
        user["sub"],
        payload.job_description_text,
        usage=usage,
    )
    background_tasks.add_task(cv_generator.compile_cv_in_background, record.id, user["sub"], record.cv_json)
    await _record_usage_after(db, cid, usage)
    return _to_response(record)


@router.post("/personalize-job", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_adapted_cv(
    payload: CVPersonalizeJobCreate,
    background_tasks: BackgroundTasks,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Adapt the user's base CV to an existing job posting (offer).

    Preconditions:
    - The user must have generated a base CV (else 422 precondition_failed).
    - The job posting must exist and belong to the user (else 404).

    The base CV is never modified; a new adapted CV document is stored.
    """
    usage: dict[str, Any] = {"tokens_input": 0, "tokens_output": 0, "cost_usd_cents": 0, "model_used": None}
    cid = await enforce_action_gate(db, user, "cv_adapted", label="Adapted CV generation")
    record = await cv_generator.adapt_cv(
        db,
        user["sub"],
        payload.base_cv_id,
        payload.job_posting_id,
        usage=usage,
    )
    background_tasks.add_task(cv_generator.compile_cv_in_background, record.id, user["sub"], record.cv_json)
    await _record_usage_after(db, cid, usage)
    return _to_response(record)


@router.post("/adapt-url", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_adapted_cv_from_url(
    payload: CVAdaptUrlCreate,
    background_tasks: BackgroundTasks,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Adapt the user's base CV to a job posting fetched live from a URL.

    Available on every plan (credit-gated): the URL is fetched server-side
    and its extracted text feeds the same adapt pipeline as the internal
    offers. The base CV is never modified; a new adapted CV is stored with
    ``job_url`` set to the source link.
    """
    usage: dict[str, Any] = {"tokens_input": 0, "tokens_output": 0, "cost_usd_cents": 0, "model_used": None}
    cid = await enforce_action_gate(db, user, "cv_adapted", label="CV adaptation by URL")
    record = await cv_generator.adapt_cv_from_url(
        db,
        user["sub"],
        payload.base_cv_id,
        payload.url,
        usage=usage,
    )
    background_tasks.add_task(cv_generator.compile_cv_in_background, record.id, user["sub"], record.cv_json)
    await _record_usage_after(db, cid, usage)
    return _to_response(record)


@router.get("/", response_model=list[CVResponse])
async def list_cvs(
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the authenticated user's generated CVs, newest first."""
    records = await cv_generator.list_cvs(db, user["sub"])
    return [_to_response(r) for r in records]


@router.get("/{cv_id}", response_model=CVResponse)
async def get_cv(
    cv_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single CV by ID (JSON + analysis)."""
    record = await cv_generator.get_cv(db, user["sub"], cv_id)
    return _to_response(record)


@router.get("/{cv_id}/download")
async def download_cv(
    cv_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download the compiled PDF for a CV.

    When R2 is configured, redirects to a signed URL.
    Otherwise, serves the file from local disk.
    """
    record = await cv_generator.get_cv(db, user["sub"], cv_id)
    if not record.pdf_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Compiled PDF not found for this CV.",
        )
    # R2 configured: redirect to signed URL
    if r2_storage._r2_configured():
        if not r2_storage.object_exists(record.pdf_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PDF not found in storage.",
            )
        signed_url = r2_storage.generate_signed_url(record.pdf_path)
        return RedirectResponse(url=signed_url, status_code=status.HTTP_302_FOUND)
    # Fallback: local disk (recompile on-demand if ephemeral disk wiped the file)
    pdf_path = resolve_pdf_path(record)
    if pdf_path is None or not pdf_path.exists():
        if record.cv_json:
            pdf_path = await cv_generator.recompile_pdf_sync(db, record)
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Compiled PDF not found for this CV.",
            )
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"cv_{record.id}.pdf",
    )


@router.delete("/{cv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cv(
    cv_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a CV: remove its PDF and hide the row."""
    await cv_generator.soft_delete_cv(db, user["sub"], cv_id)
    return None


@router.post("/{cv_id}/refresh-url")
async def refresh_cv_pdf_url(
    cv_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    """Regenerate the signed URL for a CV's PDF.

    Useful when the previous URL expired (default TTL: 1h).
    Does not recompile the PDF — only generates a new signed URL.
    """
    record = await cv_generator.get_cv(db, user["sub"], cv_id)
    if not record.pdf_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PDF not available for this CV.",
        )
    url = r2_storage.generate_signed_url(record.pdf_path) if r2_storage._r2_configured() else build_pdf_url(record)
    return {"pdf_url": url}
