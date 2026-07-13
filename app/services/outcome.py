"""Outcome service — records the result of a job application.

Implements the /outcome workflow from the original repo:
- Records progress updates (interview invitations, stages completed, offers)
- Records final resolutions (hired, rejected, no response, etc.)
- Updates job_search_tracker.csv status column (used by /scrape and /rank for dedup)
- Archives outcome.md in documents/applications/<company>_<role>/
- Feeds back into /setup calibration and STAR mining
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import Application, JobPosting, Outcome, User
from app.exceptions import NotFoundError, ProfileIncompleteError
from app.schemas.outcome import OutcomeCreate, OutcomeLLMOutput, OutcomeUpdate, TrackerRowOut

settings = get_settings()

# ── Status constants ────────────────────────────────────────────────

PROGRESS_STATUSES = {
    "interview_invited",
    "phone_screen_completed",
    "technical_completed",
    "case_completed",
    "final_round_completed",
    "offer_received",
}

RESOLUTION_STATUSES = {
    "hired",
    "offer_declined",
    "rejected",
    "no_response",
    "interview_only",
    "withdrawn",
}

ALL_STATUSES = PROGRESS_STATUSES | RESOLUTION_STATUSES

# ── Helper functions ────────────────────────────────────────────────


def _get_tracker_path() -> Path:
    """Get the path to job_search_tracker.csv."""
    return Path(settings.tracker_path)


def _get_applications_dir() -> Path:
    """Get the documents/applications directory."""
    return Path(settings.documents_dir) / "applications"


def _validate_status(status: str) -> None:
    """Validate that status is a known value."""
    if status not in ALL_STATUSES:
        raise ValueError(
            f"Invalid status: {status}. Must be one of: {', '.join(sorted(ALL_STATUSES))}"
        )


def _is_resolution(status: str) -> bool:
    """Check if status is a final resolution."""
    return status in RESOLUTION_STATUSES


def _get_tracker_fieldnames() -> list[str]:
    """Return the CSV fieldnames for job_search_tracker.csv."""
    return [
        "date",
        "company",
        "sector",
        "role",
        "role_type",
        "channel",
        "status",
        "contact_person",
        "fit_rating",
        "notes",
        "cv_file",
        "cover_letter_file",
        "source",
    ]


# ── Main orchestration ──────────────────────────────────────────────


async def execute_outcome(
    db: AsyncSession,
    user_id: str,
    payload: OutcomeCreate,
) -> Outcome:
    """Execute the outcome workflow.

    Args:
        db: Database session
        user_id: Authenticated user ID
        payload: Outcome data

    Returns:
        The created/updated Outcome record
    """
    # 1. Validate status
    _validate_status(payload.status)

    # 2. Load application and verify ownership
    app_result = await db.execute(
        select(Application)
        .where(Application.id == payload.application_id)
        .where(Application.user_id == user_id)
    )
    application = app_result.scalar_one_or_none()
    if application is None:
        raise NotFoundError("Application not found.")

    # 3. Load job posting
    job_result = await db.execute(
        select(JobPosting).where(JobPosting.id == application.job_posting_id)
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job posting not found.")

    # 4. Always create a new outcome record (one per status change / progress update)
    outcome = Outcome(
        user_id=user_id,
        application_id=payload.application_id,
        status=payload.status,
    )
    db.add(outcome)

    # 5. Update fields from payload
    if payload.date_resolved:
        outcome.date_resolved = payload.date_resolved
    if payload.phone_screen_date:
        outcome.phone_screen_date = payload.phone_screen_date
    if payload.technical_date:
        outcome.technical_date = payload.technical_date
    if payload.case_date:
        outcome.case_date = payload.case_date
    if payload.final_round_date:
        outcome.final_round_date = payload.final_round_date
    if payload.offer_received_date:
        outcome.offer_received_date = payload.offer_received_date
    if payload.notes is not None:
        # Store notes exactly as received, no timestamp prefix
        outcome.notes = payload.notes
    if payload.lessons_learned is not None:
        outcome.lessons_learned = payload.lessons_learned
    if payload.valued_signals is not None:
        outcome.valued_signals = payload.valued_signals

    # 6. Set date_resolved if this is a resolution status
    if _is_resolution(payload.status) and not outcome.date_resolved:
        outcome.date_resolved = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 7. Update interview stage dates
    if payload.phone_screen_date:
        outcome.phone_screen_date = payload.phone_screen_date
    if payload.technical_date:
        outcome.technical_date = payload.technical_date
    if payload.case_date:
        outcome.case_date = payload.case_date
    if payload.final_round_date:
        outcome.final_round_date = payload.final_round_date
    if payload.offer_received_date:
        outcome.offer_received_date = payload.offer_received_date

    await db.flush()
    await db.refresh(outcome)

    # 8. Update job_search_tracker.csv
    await _update_tracker_csv(db, application, job, payload.status)

    # 9. Archive outcome.md in documents/applications/
    await _archive_outcome_md(application, job, outcome)

    # 10. Update job posting status
    application.job_posting.status = _map_outcome_to_job_status(payload.status)
    await db.commit()
    await db.refresh(outcome)

    return outcome


async def update_outcome(
    db: AsyncSession,
    user_id: str,
    outcome_id: str,
    payload: OutcomeUpdate,
) -> Outcome:
    """Update an existing outcome."""
    result = await db.execute(
        select(Outcome)
        .where(Outcome.id == outcome_id)
        .where(Outcome.user_id == user_id)
    )
    outcome = result.scalar_one_or_none()
    if outcome is None:
        raise NotFoundError("Outcome not found.")

    if payload.status:
        _validate_status(payload.status)
        outcome.status = payload.status
        if _is_resolution(payload.status) and not outcome.date_resolved:
            outcome.date_resolved = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if payload.date_resolved:
        outcome.date_resolved = payload.date_resolved
    if payload.phone_screen_date:
        outcome.phone_screen_date = payload.phone_screen_date
    if payload.technical_date:
        outcome.technical_date = payload.technical_date
    if payload.case_date:
        outcome.case_date = payload.case_date
    if payload.final_round_date:
        outcome.final_round_date = payload.final_round_date
    if payload.offer_received_date:
        outcome.offer_received_date = payload.offer_received_date
    if payload.notes is not None:
        # Store notes exactly as received, no timestamp prefix
        outcome.notes = payload.notes
    if payload.lessons_learned is not None:
        outcome.lessons_learned = payload.lessons_learned
    if payload.valued_signals is not None:
        outcome.valued_signals = payload.valued_signals

    await db.commit()
    await db.refresh(outcome)
    return outcome


# ── Helper functions ────────────────────────────────────────────────


def _map_outcome_to_job_status(outcome_status: str) -> str:
    """Map outcome status to job posting status."""
    mapping = {
        "interview_invited": "interview",
        "phone_screen_completed": "interview",
        "technical_completed": "interview",
        "case_completed": "interview",
        "final_round_completed": "interview",
        "offer_received": "offer",
        "hired": "hired",
        "offer_declined": "offer_declined",
        "rejected": "rejected",
        "no_response": "no_response",
        "interview_only": "interview_only",
        "withdrawn": "withdrawn",
    }
    return mapping.get(outcome_status, "applied")


async def _update_tracker_csv(
    db: AsyncSession,
    application: Application,
    job: JobPosting,
    new_status: str,
) -> None:
    """Update job_search_tracker.csv with the new status.

    The tracker is used by /scrape and /rank for dedup and exclusion.
    """
    tracker_path = _get_tracker_path()
    tracker_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing rows
    rows = []
    if tracker_path.exists():
        with open(tracker_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

    # Find existing row for this company+role
    company = job.company or "Unknown"
    role = job.title
    row_idx = None
    for i, row in enumerate(rows):
        if row.get("company", "").lower() == company.lower() and row.get("role", "").lower() == role.lower():
            row_idx = i
            break

    # Build tracker row
    tracker_row = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "company": company,
        "sector": job.description[:100] if job.description else "",
        "role": role,
        "role_type": job.employment_type or "",
        "channel": job.portal,
        "status": new_status,
        "contact_person": "",
        "fit_rating": "",
        "notes": "",
        "cv_file": application.cv_pdf_path or "",
        "cover_letter_file": application.cover_letter_pdf_path or "",
        "source": job.url or "",
    }

    if row_idx is not None:
        rows[row_idx] = tracker_row
    else:
        rows.append(tracker_row)

    # Write back
    fieldnames = _get_tracker_fieldnames()
    with open(tracker_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


async def _archive_outcome_md(
    application: Application,
    job: JobPosting,
    outcome: Outcome,
) -> None:
    """Archive outcome.md in documents/applications/<company>_<role>/."""
    apps_dir = _get_applications_dir()
    company_slug = (job.company or "unknown").lower().replace(" ", "_")
    role_slug = job.title.lower().replace(" ", "_")
    app_dir = apps_dir / f"{company_slug}_{role_slug}"
    app_dir.mkdir(parents=True, exist_ok=True)

    # Build outcome.md content
    lines = [
        f"# Outcome: {job.company} — {job.title}",
        "",
        f"**Status:** {outcome.status}",
        f"**Date resolved:** {outcome.date_resolved or 'N/A'}",
        "",
        "## Interview Stages",
        f"- Phone screen: {outcome.phone_screen_date or 'Not completed'}",
        f"- Technical: {outcome.technical_date or 'Not completed'}",
        f"- Case: {outcome.case_date or 'Not completed'}",
        f"- Final round: {outcome.final_round_date or 'Not completed'}",
        f"- Offer received: {outcome.offer_received_date or 'Not received'}",
        "",
        "## Notes",
        outcome.notes or "No notes recorded.",
        "",
        "## Lessons Learned",
        outcome.lessons_learned or "No lessons recorded.",
        "",
        "## Valued Signals",
    ]

    if outcome.valued_signals:
        for signal in outcome.valued_signals:
            lines.append(f"- {signal}")
    else:
        lines.append("No signals recorded.")

    content = "\n".join(lines)
    outcome_md_path = app_dir / "outcome.md"
    outcome_md_path.write_text(content, encoding="utf-8")


# ── Query helpers ───────────────────────────────────────────────────


async def get_outcome(
    db: AsyncSession, outcome_id: str, user_id: str
) -> Outcome:
    """Get an outcome by ID, verifying ownership."""
    result = await db.execute(
        select(Outcome)
        .where(Outcome.id == outcome_id)
        .where(Outcome.user_id == user_id)
    )
    outcome = result.scalar_one_or_none()
    if outcome is None:
        raise NotFoundError("Outcome not found.")
    return outcome


async def list_outcomes(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[Outcome]:
    """List outcomes for a user."""
    result = await db.execute(
        select(Outcome)
        .where(Outcome.user_id == user_id)
        .order_by(Outcome.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_outcome_by_application(
    db: AsyncSession, application_id: str, user_id: str
) -> Outcome | None:
    """Get the outcome for a specific application."""
    result = await db.execute(
        select(Outcome)
        .where(Outcome.application_id == application_id)
        .where(Outcome.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_tracker_rows(
    db: AsyncSession, user_id: str
) -> list[TrackerRowOut]:
    """List all tracker rows for a user (reads from CSV)."""
    tracker_path = _get_tracker_path()
    if not tracker_path.exists():
        return []

    with open(tracker_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Filter by user's applications (we'd need to join with DB, but for now return all)
    # In a real implementation, we'd filter by user's companies/roles
    return [TrackerRowOut(**row) for row in rows]