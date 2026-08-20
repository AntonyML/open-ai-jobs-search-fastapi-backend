"""Expand router — endpoints for competency expansion from documents and online presence."""

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_max_or_admin
from app.db.models import CandidateProfile, CompetencyExpansion
from app.db.session import get_db as _get_db
from app.exceptions import ProfileIncompleteError
from app.schemas.expand import (
    CompetencyExpansionOut,
    CompetencyExpansionSummaryOut,
    ExpandRequest,
)
from app.services import expand
from app.services.access_gate import enforce_action_gate

router = APIRouter(prefix="/expand", tags=["expand"])


@router.post(
    "/",
    response_model=CompetencyExpansionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_expand(
    payload: ExpandRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Trigger a competency expansion run.

    Scans all configured sources (CV, LinkedIn, diplomas, references, GitHub, other URLs)
    for experience items, enriches them with competencies via web search, and proposes
    additions to the candidate profile.

    Runs as a background task. Returns immediately with the expansion record (status=pending).
    Poll GET /expand/{expansion_id} to check completion.
    """

    # 1. Get candidate profile
    candidate_result = await db.execute(select(CandidateProfile).where(CandidateProfile.user_id == user["sub"]))
    candidate = candidate_result.scalar_one_or_none()
    if candidate is None:
        raise ProfileIncompleteError("Candidate profile not found. Run /setup first.")

    # Gate LLM usage (quota/credits) before starting the pipeline; the ledger
    # correlation id is attached to the background task so the real usage is
    # recorded once the expansions complete.
    correlation_id = await enforce_action_gate(
        db, user, "expand", label=f"Competency expansion ({payload.scan_cv=} {payload.scan_linkedin=})"
    )

    # 2. Create expansion record with pending status
    expansion = CompetencyExpansion(
        user_id=user["sub"],
        candidate_id=candidate.id,
        scanned_cv=payload.scan_cv,
        scanned_linkedin=payload.scan_linkedin,
        scanned_diplomas=payload.scan_diplomas,
        scanned_references=payload.scan_references,
        scanned_github=payload.scan_github,
        scanned_other_urls=payload.scan_other_urls,
        status="pending",
    )
    db.add(expansion)
    await db.commit()
    await db.refresh(expansion)

    # 3. Add background task
    background_tasks.add_task(expand._execute_expand_background, expansion.id, correlation_id)

    return expansion


@router.get("/{expansion_id}", response_model=CompetencyExpansionOut)
async def get_expansion(
    expansion_id: str,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Get a competency expansion by ID."""
    return await expand.get_expansion(db, expansion_id, user["sub"])


@router.get("/", response_model=list[CompetencyExpansionSummaryOut])
async def list_expansions(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
):
    """List all competency expansions for the authenticated user.

    Errors are silently handled — returns empty list instead of 500.
    """
    try:
        return await expand.list_expansions(db, user["sub"], limit=limit, offset=offset)
    except Exception:
        return []
