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

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models import GeneratedCV
from app.schemas.cv import (
    CVAnalysis,
    CVAdaptUrlCreate,
    CVBaseCreate,
    CVJobOut,
    CVPersonalizeCreate,
    CVPersonalizeJobCreate,
    CVRecoverCreate,
    CVResponse,
)
from app.services import credits, cv_generator
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
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a generic base CV from the candidate profile."""
    usage: dict[str, Any] = {"tokens_input": 0, "tokens_output": 0, "cost_usd_cents": 0, "model_used": None}
    cid = await enforce_action_gate(db, user, "cv_base", label="Base CV generation")
    record = await cv_generator.generate_base_cv(db, user["sub"], usage=usage)
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
    record = await cv_generator.recover_previous_base(
        db, user["sub"], payload.cv_id
    )
    return _to_response(record)


@router.post("/personalize", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_personalized_cv(
    payload: CVPersonalizeCreate,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tailor a CV to a free-text job description (no scraping, no URL)."""
    usage: dict[str, Any] = {"tokens_input": 0, "tokens_output": 0, "cost_usd_cents": 0, "model_used": None}
    cid = await enforce_action_gate(db, user, "cv_base", label="Personalized CV generation")
    record = await cv_generator.personalize_cv(
        db, user["sub"], payload.job_description_text, usage=usage,
    )
    await _record_usage_after(db, cid, usage)
    return _to_response(record)


@router.post("/personalize-job", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_adapted_cv(
    payload: CVPersonalizeJobCreate,
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
        db, user["sub"], payload.base_cv_id, payload.job_posting_id, usage=usage,
    )
    await _record_usage_after(db, cid, usage)
    return _to_response(record)


@router.post("/adapt-url", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_adapted_cv_from_url(
    payload: CVAdaptUrlCreate,
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
        db, user["sub"], payload.base_cv_id, payload.url, usage=usage,
    )
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
    """Download the compiled PDF for a CV."""
    record = await cv_generator.get_cv(db, user["sub"], cv_id)
    pdf_path = resolve_pdf_path(record)
    if pdf_path is None or not pdf_path.exists():
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
    """Soft-delete a CV (keeps the PDF on disk for existing downloads)."""
    await cv_generator.soft_delete_cv(db, user["sub"], cv_id)
    return None
