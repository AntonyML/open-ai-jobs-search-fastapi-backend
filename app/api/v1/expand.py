"""Expand router — endpoints for competency expansion from documents and online presence."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.session import get_db as _get_db
from app.schemas.expand import (
    CompetencyExpansionOut,
    CompetencyExpansionSummaryOut,
    ExpandRequest,
)
from app.services import expand

router = APIRouter(prefix="/expand", tags=["expand"])


@router.post(
    "/",
    response_model=CompetencyExpansionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_expand(
    payload: ExpandRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Trigger a competency expansion run.

    Scans all configured sources (CV, LinkedIn, diplomas, references, GitHub, other URLs)
    for experience items, enriches them with competencies via web search, and proposes
    additions to the candidate profile.

    Runs synchronously in the request. For production, consider moving to a background task.
    """
    result = await expand.execute_expand(
        db=db,
        user_id=user["sub"],
        scan_cv=payload.scan_cv,
        scan_linkedin=payload.scan_linkedin,
        scan_diplomas=payload.scan_diplomas,
        scan_references=payload.scan_references,
        scan_github=payload.scan_github,
        scan_other_urls=payload.scan_other_urls,
    )
    return result


@router.get("/{expansion_id}", response_model=CompetencyExpansionOut)
async def get_expansion(
    expansion_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get a competency expansion by ID."""
    return await expand.get_expansion(db, expansion_id, user["sub"])


@router.get("/", response_model=list[CompetencyExpansionSummaryOut])
async def list_expansions(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all competency expansions for the authenticated user."""
    return await expand.list_expansions(db, user["sub"], limit=limit, offset=offset)