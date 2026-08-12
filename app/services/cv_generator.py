"""CV generator service — generic base CVs and job-tailored CVs.

FASE 1 of the CV generator feature.  Endpoints in ``app/api/v1/cv.py`` call
into this service, which:

1. Loads the authenticated user's candidate profile (``/setup`` must run first).
2. Resolves the user's active LLM provider credentials.
3. Generates a ``GenerateCVOutput`` dict — either a generic base CV
   (``POST /cv/base``) or a job-tailored CV from free-text job description
   (``POST /cv/personalize``).
4. Compiles it to PDF via Typst and persists a ``GeneratedCV`` row.

The personalize flow never touches ``JobPosting``: the job description text is
passed straight to the LLM (recruiter-lens analysis → drafter).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.settings import get_settings
from app.db.models import CandidateProfile, GeneratedCV, JobPosting
from app.exceptions import (
    NotFoundError,
    PreconditionError,
    ProfileIncompleteError,
    ProviderAuthError,
)
from app.services.apply_json import (
    adapt_cv_llm,
    generate_base_cv_llm,
    personalize_cv_llm,
)
from app.services.pdf_compiler_typst import compile_cv
from app.services.provider_config import get_active_provider_config
from app.services.setup import get_profile

# Free tier: max CVs generated per rolling hour.
FREE_TIER_CVS_PER_HOUR = 5


# ── Profile / provider resolution ────────────────────────────────────


async def _load_profile_or_raise(db: AsyncSession, user_id: str) -> CandidateProfile:
    """Return the candidate profile, or a 422 if /setup has not been run."""
    try:
        return await get_profile(db, user_id)
    except NotFoundError:
        raise ProfileIncompleteError(
            "Complete your profile setup first (POST /setup)."
        ) from None


async def _resolve_provider_or_raise(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Return the global provider config, or a 400 if none is configured."""
    config = await get_active_provider_config(db)
    if not config or config.get("provider") is None:
        raise ProviderAuthError(
            "No LLM provider configured. An admin must configure the global provider first."
        )
    return config


# ── Generation ───────────────────────────────────────────────────────


async def generate_base_cv(db: AsyncSession, user_id: str) -> GeneratedCV:
    """Generate a generic base CV (no job context) and persist it."""
    profile = await _load_profile_or_raise(db, user_id)
    provider_config = await _resolve_provider_or_raise(db, user_id)

    output_dict = await generate_base_cv_llm(profile, provider_config)
    return await _persist_and_compile(
        db, user_id,
        cv_type="base",
        output_dict=output_dict,
        analysis_dict=None,
        job_description_text=None,
    )


async def personalize_cv(
    db: AsyncSession,
    user_id: str,
    job_description_text: str,
) -> GeneratedCV:
    """Tailor a CV to a free-text job description and persist it."""
    profile = await _load_profile_or_raise(db, user_id)
    provider_config = await _resolve_provider_or_raise(db, user_id)

    analysis_dict, output_dict = await personalize_cv_llm(
        profile, job_description_text, provider_config,
    )
    return await _persist_and_compile(
        db, user_id,
        cv_type="personalized",
        output_dict=output_dict,
        analysis_dict=analysis_dict,
        job_description_text=job_description_text,
    )


async def adapt_cv(
    db: AsyncSession,
    user_id: str,
    base_cv_id: str,
    job_posting_id: str,
) -> GeneratedCV:
    """Adapt the user's base CV to an existing job posting.

    Business rules enforced here:
    - Rule 4: a base CV must exist before any adapted CV can be generated.
    - Rule 5: the adapted CV is generated from the base CV + the job posting.
    - Rule 6: the base CV record is never modified — a NEW document is stored.
    - Rule 7: multiple adapted CVs (one per offer) are supported.
    """
    profile = await _load_profile_or_raise(db, user_id)
    provider_config = await _resolve_provider_or_raise(db, user_id)

    # Rule 4 — base CV required
    result = await db.execute(
        select(GeneratedCV).where(
            GeneratedCV.id == base_cv_id,
            GeneratedCV.user_id == user_id,
            GeneratedCV.is_deleted.is_(False),
        )
    )
    base_cv = result.scalar_one_or_none()
    if base_cv is None or base_cv.cv_type != "base":
        raise PreconditionError(
            "Generate a base CV first before adapting it to a job offer."
        )

    job = await db.get(JobPosting, job_posting_id)
    if job is None or job.user_id != user_id:
        raise NotFoundError("Job posting not found.")

    analysis_dict, output_dict = await adapt_cv_llm(
        profile, base_cv.cv_json, job, provider_config,
    )
    return await _persist_and_compile(
        db, user_id,
        cv_type="personalized",
        output_dict=output_dict,
        analysis_dict=analysis_dict,
        job_description_text=job.description,
        job_posting_id=job.id,
        job_url=job.url,
    )


async def _persist_and_compile(
    db: AsyncSession,
    user_id: str,
    *,
    cv_type: str,
    output_dict: dict[str, Any],
    analysis_dict: dict[str, Any] | None,
    job_description_text: str | None,
    job_posting_id: str | None = None,
    job_url: str | None = None,
) -> GeneratedCV:
    """Compile the PDF, persist the record, and return it.

    The PDF is written to ``{cv_storage_path}/{user_id}/{cv_id}.pdf`` (relative
    to the working directory, matching how /apply stores files).  ``pdf_path``
    keeps that relative path so the download route can resolve it.
    """
    settings = get_settings()
    cv_id = str(uuid.uuid4())

    pdf_path: Path | None = None
    if cv_type == "base" or output_dict.get("cv"):
        rel_dir = Path(settings.cv_storage_path) / user_id
        rel_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = rel_dir / f"{cv_id}.pdf"
        try:
            compile_cv(output_dict, output=pdf_path)
        except Exception:
            pdf_path = None  # LLM output stored; PDF compile failure is non-fatal

    record = GeneratedCV(
        id=cv_id,
        user_id=user_id,
        cv_type=cv_type,
        job_posting_id=job_posting_id,
        job_url=job_url,
        job_description_text=job_description_text,
        cv_json=output_dict,
        pdf_path=str(pdf_path) if pdf_path else None,
        analysis=analysis_dict,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


# ── Queries / lifecycle ──────────────────────────────────────────────


async def list_cvs(db: AsyncSession, user_id: str) -> list[GeneratedCV]:
    """Return the user's non-deleted CVs, newest first."""
    result = await db.execute(
        select(GeneratedCV)
        .options(selectinload(GeneratedCV.job_posting))
        .where(GeneratedCV.user_id == user_id, GeneratedCV.is_deleted.is_(False))
        .order_by(GeneratedCV.created_at.desc())
    )
    return list(result.scalars().all())


async def get_cv(db: AsyncSession, user_id: str, cv_id: str) -> GeneratedCV:
    """Return one of the user's non-deleted CVs, or 404."""
    result = await db.execute(
        select(GeneratedCV)
        .options(selectinload(GeneratedCV.job_posting))
        .where(
            GeneratedCV.id == cv_id,
            GeneratedCV.user_id == user_id,
            GeneratedCV.is_deleted.is_(False),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("CV not found.")
    return record


async def soft_delete_cv(db: AsyncSession, user_id: str, cv_id: str) -> None:
    """Soft-delete a CV so it disappears from the list but the PDF stays."""
    record = await get_cv(db, user_id, cv_id)
    record.is_deleted = True
    await db.commit()


async def count_recent_cvs(
    db: AsyncSession,
    user_id: str,
    window_minutes: int = 60,
) -> int:
    """Count CVs generated by a user within a rolling window (rate limiting)."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    result = await db.execute(
        select(func.count())
        .select_from(GeneratedCV)
        .where(
            GeneratedCV.user_id == user_id,
            GeneratedCV.created_at >= cutoff,
        )
    )
    return int(result.scalar() or 0)


# ── Serialisation helpers ────────────────────────────────────────────


def resolve_pdf_path(record: GeneratedCV) -> Path | None:
    """Resolve the stored relative ``pdf_path`` to an absolute path."""
    if not record.pdf_path:
        return None
    path = Path(record.pdf_path)
    return path if path.is_absolute() else Path.cwd() / path


def build_pdf_url(record: GeneratedCV) -> str | None:
    """Build the public download URL for a CV."""
    if not record.pdf_path:
        return None
    base = get_settings().base_url.rstrip("/")
    return f"{base}/api/v1/cv/{record.id}/download"
