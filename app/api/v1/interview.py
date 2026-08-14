"""Interview router — endpoints for interview preparation."""

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_locale, require_max_or_admin
from app.core.i18n.locale import t
from app.db.models import InterviewPrep
from app.db.session import get_db as _get_db
from app.schemas.interview import InterviewPrepOut, InterviewPrepRequest, InterviewPrepSummaryOut, MockInterviewRequest, MockInterviewResponse
from app.services import interview
from app.services.access_gate import enforce_action_gate

router = APIRouter(prefix="/interview", tags=["interview"])


@router.post(
    "/",
    response_model=InterviewPrepOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_interview_prep(
    payload: InterviewPrepRequest,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
    locale: str = Depends(get_locale),
):
    """Generate interview preparation pack for an application."""
    # Gate LLM usage (quota/credits) and get a correlation_id for usage accounting.
    correlation_id = await enforce_action_gate(
        db,
        user,
        "interview",
        label="Interview preparation pack",
    )
    result = await interview.execute_interview_prep(
        db=db,
        user_id=user["sub"],
        application_id=payload.application_id,
        stage=payload.stage,
        interview_date=payload.interview_date,
        interview_format=payload.interview_format,
        interviewer_names=payload.interviewer_names,
        correlation_id=correlation_id,
    )
    return result


@router.get("/{prep_id}", response_model=InterviewPrepOut)
async def get_interview_prep(
    prep_id: str,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Get an interview preparation pack by ID."""
    return await interview.get_interview_prep(db, prep_id, user["sub"])


@router.get("/", response_model=list[InterviewPrepSummaryOut])
async def list_interview_preps(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
):
    """List all interview preparation packs for the authenticated user."""
    return await interview.list_interview_preps(db, user["sub"], limit=limit, offset=offset)


# ── Mock interview endpoints ────────────────────────────────────────


@router.post("/{prep_id}/mock", response_model=MockInterviewResponse)
async def start_or_answer_mock(
    prep_id: str,
    payload: MockInterviewRequest,
    user: dict = Depends(require_max_or_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Start a mock interview or submit an answer."""
    # If no answer provided, start the mock interview
    if not payload.user_answer:
        return await interview.start_mock_interview(db, user["sub"], prep_id)

    # Otherwise, submit an answer — each turn is an LLM call, so gate it
    correlation_id = await enforce_action_gate(
        db,
        user,
        "interview",
        label="Mock interview feedback",
    )
    # Load prep once (avoids N+1 — used by both transcript parsing and service)
    prep = await interview.get_interview_prep(db, prep_id, user["sub"])
    transcript = _parse_transcript(prep.mock_transcript)

    return await interview.submit_mock_answer(
        db, user["sub"], prep_id, payload.user_answer, prep, transcript, correlation_id
    )


def _parse_transcript(raw: str | None) -> list[dict[str, str]]:
    """Parse a saved mock_transcript string back into a list of turns."""
    if not raw:
        return []
    turns = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if line.startswith("INTERVIEWER:"):
            turns.append({"role": "interviewer", "content": line[len("INTERVIEWER:"):].strip()})
        elif line.startswith("CANDIDATE:"):
            turns.append({"role": "candidate", "content": line[len("CANDIDATE:"):].strip()})
    return turns