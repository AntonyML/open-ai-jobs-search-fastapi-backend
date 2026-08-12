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
from app.schemas.cv import CVAnalysis, CVBaseCreate, CVPersonalizeCreate, CVResponse
from app.services import cv_generator
from app.services.cv_generator import (
    FREE_TIER_CVS_PER_HOUR,
    build_pdf_url,
    count_recent_cvs,
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
    return CVResponse(
        cv_id=record.id,
        cv_type=record.cv_type,  # type: ignore[arg-type]
        job_url=record.job_url,
        job_description_text=record.job_description_text,
        json_cv=record.cv_json or {},
        pdf_url=build_pdf_url(record),
        analysis=analysis,
        created_at=record.created_at,
    )


async def _enforce_rate_limit(
    db: AsyncSession,
    user: dict[str, Any],
) -> None:
    """Free tier: max 5 CVs per hour.  Premium: unlimited."""
    tier = user.get("tier", "free")
    if tier == "premium":
        return
    recent = await count_recent_cvs(db, user["sub"], window_minutes=60)
    if recent >= FREE_TIER_CVS_PER_HOUR:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You have reached the limit of {FREE_TIER_CVS_PER_HOUR} CVs per hour "
                "on your current plan. Upgrade to Premium for unlimited CV generation."
            ),
        )


@router.post("/base", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_base_cv(
    payload: CVBaseCreate,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a generic base CV from the candidate profile."""
    await _enforce_rate_limit(db, user)
    record = await cv_generator.generate_base_cv(db, user["sub"])
    return _to_response(record)


@router.post("/personalize", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def create_personalized_cv(
    payload: CVPersonalizeCreate,
    user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tailor a CV to a free-text job description (no scraping, no URL)."""
    await _enforce_rate_limit(db, user)
    record = await cv_generator.personalize_cv(db, user["sub"], payload.job_description_text)
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
