"""CV generator service — generic base CVs and job-tailored CVs.

FASE 1 of the CV generator feature.  Endpoints in ``app/api/v1/cv.py`` call
into this service, which:

1. Loads the authenticated user's candidate profile (``/setup`` must run first).
2. Resolves the user's active LLM provider credentials.
3. Generates a ``GenerateCVOutput`` dict — either a generic base CV
   (``POST /cv/base``) or a job-tailored CV from free-text job description
   (``POST /cv/personalize``).
4. Persists a ``GeneratedCV`` row **immediately** (the JSON is the source of
   truth); the PDF is compiled asynchronously by ``compile_cv_in_background``
   (scheduled from the endpoint as a FastAPI ``BackgroundTask``) so Typst is
   never on the request's critical path.

The personalize flow never touches ``JobPosting``: the job description text is
passed straight to the LLM (recruiter-lens analysis → drafter).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.db.models import CandidateProfile, GeneratedCV, JobPosting
from app.db.session import async_session_factory
from app.exceptions import (
    NotFoundError,
    PreconditionError,
    ProfileIncompleteError,
    ProviderAuthError,
    WebSearchUnavailableError,
)
from app.services import r2_storage
from app.services.apply_json import (
    adapt_cv_llm,
    adapt_cv_llm_with_url,
    generate_base_cv_llm,
    personalize_cv_llm,
)
from app.services.artifact_store import new_output_path, remove_file, resolve_existing
from app.services.notifications import notify_admin
from app.services.pdf_compiler_typst import compile_cv
from app.services.provider_config import get_active_provider_config
from app.services.setup import get_profile

logger = get_logger(__name__)

# Free tier: max CVs generated per rolling hour.
FREE_TIER_CVS_PER_HOUR = 5


# ── Profile / provider resolution ────────────────────────────────────


async def _load_profile_or_raise(db: AsyncSession, user_id: str) -> CandidateProfile:
    """Return the candidate profile, or a 422 if /setup has not been run."""
    try:
        return await get_profile(db, user_id)
    except NotFoundError:
        raise ProfileIncompleteError("Complete your profile setup first (POST /setup).") from None


async def _resolve_provider_or_raise(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Return the global provider config, or a 400 if none is configured."""
    config = await get_active_provider_config(db)
    if not config or config.get("provider") is None:
        raise ProviderAuthError("No LLM provider configured. An admin must configure the global provider first.")
    return config


# ── Generation ───────────────────────────────────────────────────────


async def generate_base_cv(
    db: AsyncSession,
    user_id: str,
    usage: dict | None = None,
    language: str = "es",
) -> GeneratedCV:
    """Generate a generic base CV (no job context) and persist it.

    Enforces the max-2 base CV invariant: the current active base is demoted
    to ``obsolete`` and any previous obsolete base is physically removed, so
    there is never more than one active base CV and never a third document.
    The LLM is called BEFORE the demotion so a failed generation never leaves
    the user without an active base CV.
    """
    profile = await _load_profile_or_raise(db, user_id)
    provider_config = await _resolve_provider_or_raise(db, user_id)

    output_dict = await generate_base_cv_llm(profile, provider_config, usage=usage, language=language)

    # Max-2 rule: demote the current active base, hard-delete the previous one.
    await _demote_previous_bases(db, user_id)

    return await _persist_and_compile(
        db,
        user_id,
        cv_type="base",
        output_dict=output_dict,
        analysis_dict=None,
        job_description_text=None,
    )


async def personalize_cv(
    db: AsyncSession,
    user_id: str,
    job_description_text: str,
    usage: dict | None = None,
    language: str = "es",
) -> GeneratedCV:
    """Tailor a CV to a free-text job description and persist it."""
    profile = await _load_profile_or_raise(db, user_id)
    provider_config = await _resolve_provider_or_raise(db, user_id)

    analysis_dict, output_dict = await personalize_cv_llm(
        profile,
        job_description_text,
        provider_config,
        usage=usage,
        language=language,
    )
    return await _persist_and_compile(
        db,
        user_id,
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
    usage: dict | None = None,
    language: str = "es",
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
    if base_cv is None or base_cv.cv_type != "base" or base_cv.base_status != "active":
        raise PreconditionError("Generate a base CV first before adapting it to a job offer.")

    job = await db.get(JobPosting, job_posting_id)
    if job is None or job.user_id != user_id:
        raise NotFoundError("Job posting not found.")

    analysis_dict, output_dict = await adapt_cv_llm(
        profile,
        base_cv.cv_json,
        job,
        provider_config,
        usage=usage,
        language=language,
    )
    return await _persist_and_compile(
        db,
        user_id,
        cv_type="personalized",
        output_dict=output_dict,
        analysis_dict=analysis_dict,
        job_description_text=job.description,
        job_posting_id=job.id,
        job_url=job.url,
    )


async def adapt_cv_from_url(
    db: AsyncSession,
    user_id: str,
    base_cv_id: str,
    url: str,
    usage: dict | None = None,
    language: str = "es",
) -> GeneratedCV:
    """Adapt the user's base CV to a job posting referenced by URL.

    Available on every plan (credit-gated): the user pastes a public job link
    and the URL is passed to the model in the prompt — the provider's
    ``web_search`` tool reads the page under its own agreements. Our backend
    never scrapes.

    Rules enforced are the same as ``adapt_cv``: the base CV must exist and
    be ``active`` (Rule 4), and the base record is never modified — a new
    ``personalized`` document is stored (Rules 5-7).
    """
    profile = await _load_profile_or_raise(db, user_id)
    provider_config = await _resolve_provider_or_raise(db, user_id)

    result = await db.execute(
        select(GeneratedCV).where(
            GeneratedCV.id == base_cv_id,
            GeneratedCV.user_id == user_id,
            GeneratedCV.is_deleted.is_(False),
        )
    )
    base_cv = result.scalar_one_or_none()
    if base_cv is None or base_cv.cv_type != "base" or base_cv.base_status != "active":
        raise PreconditionError("Generate a base CV first before adapting it to a job offer.")

    # The model reads the URL (provider web_search). If the configured model
    # can't open links, this raises WebSearchUnavailableError before any LLM
    # call — the admin is notified so the config issue gets fixed.
    try:
        analysis_dict, output_dict = await adapt_cv_llm_with_url(
            profile,
            base_cv.cv_json,
            url,
            provider_config,
            usage=usage,
            language=language,
        )
    except WebSearchUnavailableError:
        await notify_admin(
            db,
            "provider_config_issue",
            "URL adaptation unavailable: model has no web search",
            (
                f"User {user_id} tried to adapt a CV by URL but the configured "
                f"model {provider_config.get('provider')}/{provider_config.get('model')} "
                "cannot open links. Configure a web-search capable model "
                "(e.g. OpenAI gpt-5) to enable the feature."
            ),
            payload={
                "user_id": user_id,
                "provider": provider_config.get("provider"),
                "model": provider_config.get("model"),
                "url": url,
            },
        )
        raise

    return await _persist_and_compile(
        db,
        user_id,
        cv_type="personalized",
        output_dict=output_dict,
        analysis_dict=analysis_dict,
        job_description_text=None,
        job_url=url,
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
    """Persist the record and return it.

    The record is saved with ``pdf_path=None`` — the JSON (``cv_json``) is the
    source of truth and the API responds as soon as it is validated.  The
    endpoint schedules ``compile_cv_in_background`` (FastAPI BackgroundTask),
    which writes ``{cv_storage_path}/{user_id}/{cv_id}.pdf`` and updates the
    row once Typst finishes.
    """
    cv_id = str(uuid.uuid4())

    record = GeneratedCV(
        id=cv_id,
        user_id=user_id,
        cv_type=cv_type,
        base_status="active" if cv_type == "base" else None,
        job_posting_id=job_posting_id,
        job_url=job_url,
        job_description_text=job_description_text,
        cv_json=output_dict,
        pdf_path=None,
        analysis=analysis_dict,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


# Semaphore to protect CPU on constrained environments (e.g. Render free tier 0.1 CPU)
TYPST_SEMAPHORE = asyncio.Semaphore(2)


async def compile_cv_in_background(
    record_id: str,
    user_id: str,
    cv_json: dict[str, Any],
    *,
    session_factory: Any = None,
) -> None:
    """Compile a CV's JSON to PDF off the request path (CAPA 4).

    Scheduled from the endpoints as a FastAPI ``BackgroundTask``.  Runs the
    blocking, in-process Typst compile in a worker thread (``asyncio.to_thread``)
    guarded by a concurrency semaphore so the event loop is never frozen and
    CPU limits are respected.
    """
    abs_pdf_path, pdf_path = new_output_path("cv", user_id, f"{record_id}.pdf")
    try:
        async with TYPST_SEMAPHORE:
            await asyncio.to_thread(compile_cv, cv_json, output=abs_pdf_path)
    except Exception:
        logger.exception("PDF compile failed for CV %s (user %s)", record_id, user_id)
        try:
            factory = session_factory or async_session_factory
            async with factory() as db:
                await notify_admin(
                    db,
                    "cv_pdf_compile_failed",
                    "PDF compile failed",
                    (f"CV {record_id} could not be rendered to PDF. The structured JSON is still available."),
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to notify admin about PDF compile failure")
        return

    # Validate PDF before uploading
    pdf_bytes = abs_pdf_path.read_bytes()
    if len(pdf_bytes) < 100 or not pdf_bytes.startswith(b"%PDF"):
        logger.error("Compiled PDF is invalid for CV %s", record_id)
        abs_pdf_path.unlink(missing_ok=True)
        return

    # Upload to R2 (if configured), fallback to local disk
    r2_upload_failed = False
    if r2_storage._r2_configured():
        try:
            r2_storage.upload_pdf(pdf_path, pdf_bytes)
            abs_pdf_path.unlink(missing_ok=True)
        except Exception:
            r2_upload_failed = True
            logger.exception("R2 upload failed for CV %s — keeping local", record_id)
    else:
        logger.debug("R2 not configured — PDF kept on local disk")

    factory = session_factory or async_session_factory
    async with factory() as db:
        record = await db.get(GeneratedCV, record_id)
        if record is None or record.is_deleted:
            if r2_storage._r2_configured():
                r2_storage.delete_pdf(pdf_path)
            else:
                remove_file("cv", pdf_path)
            return
        record.pdf_path = pdf_path
        await db.commit()

        # Notify admin if R2 upload failed (non-blocking)
        if r2_upload_failed:
            try:
                await notify_admin(
                    db,
                    type_="r2_upload_failed",
                    title="R2 upload failed",
                    body=f"PDF for CV {record_id} could not be uploaded to R2. PDF kept on local disk.",
                    payload={"record_id": record_id, "pdf_path": pdf_path},
                )
            except Exception:
                logger.warning("Failed to send admin notification for R2 upload failure")


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


# ── Base CV lifecycle (max-2 invariant) ────────────────────────────


async def _user_base_cvs(db: AsyncSession, user_id: str) -> list[GeneratedCV]:
    """All of the user's non-deleted base CVs, oldest first."""
    result = await db.execute(
        select(GeneratedCV)
        .where(
            GeneratedCV.user_id == user_id,
            GeneratedCV.cv_type == "base",
            GeneratedCV.is_deleted.is_(False),
        )
        .order_by(GeneratedCV.created_at.asc())
    )
    return list(result.scalars().all())


async def _user_active_base_cv(db: AsyncSession, user_id: str) -> GeneratedCV | None:
    """The user's current (active) base CV, if any."""
    result = await db.execute(
        select(GeneratedCV)
        .where(
            GeneratedCV.user_id == user_id,
            GeneratedCV.cv_type == "base",
            GeneratedCV.is_deleted.is_(False),
            GeneratedCV.base_status == "active",
        )
        .order_by(GeneratedCV.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _user_obsolete_base_cv(db: AsyncSession, user_id: str) -> GeneratedCV | None:
    """The user's previous (obsolete) base CV, if any."""
    result = await db.execute(
        select(GeneratedCV)
        .where(
            GeneratedCV.user_id == user_id,
            GeneratedCV.cv_type == "base",
            GeneratedCV.is_deleted.is_(False),
            GeneratedCV.base_status == "obsolete",
        )
        .order_by(GeneratedCV.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _hard_delete_base(db: AsyncSession, record: GeneratedCV) -> None:
    """Physically remove a base CV: PDF from storage (best-effort) + DB row.

    This is the only path that actually frees space under ``generated_cvs/``.
    """
    if r2_storage._r2_configured():
        r2_storage.delete_pdf(record.pdf_path)
    else:
        remove_file("cv", record.pdf_path)
    await db.delete(record)


def _remove_pdf_file(cv: GeneratedCV) -> None:
    """Best-effort removal of the PDF file from storage.

    The PDF is a derived artifact (``cv_json`` is the source of truth), so
    removal is safe.  Delete is idempotent (missing files are fine).
    """
    if not cv.pdf_path:
        return
    if r2_storage._r2_configured():
        r2_storage.delete_pdf(cv.pdf_path)
    else:
        remove_file("cv", cv.pdf_path)


async def _demote_previous_bases(db: AsyncSession, user_id: str) -> None:
    """Enforce the max-2 rule before persisting a new base CV.

    Every obsolete base is hard-deleted (a replaced CV is never kept past the
    next regeneration) and every active base is demoted to ``obsolete``. As
    long as the invariant held, this keeps exactly one active + one obsolete.
    """
    bases = await _user_base_cvs(db, user_id)
    if not bases:
        return
    for base in bases:
        if base.base_status == "obsolete":
            await _hard_delete_base(db, base)
        else:
            base.base_status = "obsolete"
    await db.commit()


async def recover_previous_base(
    db: AsyncSession,
    user_id: str,
    previous_cv_id: str,
) -> GeneratedCV:
    """Swap the previous (obsolete) base CV back to active.

    Validates ownership and that the target is a non-deleted, obsolete base
    CV. The current active base is demoted to ``obsolete``, so the swap never
    creates a third document and there is always exactly one active base.
    """
    result = await db.execute(
        select(GeneratedCV).where(
            GeneratedCV.id == previous_cv_id,
            GeneratedCV.user_id == user_id,
            GeneratedCV.is_deleted.is_(False),
        )
    )
    previous = result.scalar_one_or_none()
    if previous is None or previous.cv_type != "base" or previous.base_status != "obsolete":
        raise PreconditionError("The previous base CV is not available to restore.")

    active = await _user_active_base_cv(db, user_id)
    if active is not None and active.id != previous.id:
        active.base_status = "obsolete"
    previous.base_status = "active"
    await db.commit()
    await db.refresh(previous)
    return previous


async def soft_delete_cv(db: AsyncSession, user_id: str, cv_id: str) -> None:
    """Delete a CV.

    Soft-delete: mark the row as deleted; the PDF is removed from disk
    immediately (it's a derived artifact, re-compilable from ``cv_json``).
    The row is retained for audit/retention; the sweeper purges it later.

    - Personalized CVs: soft-delete (hidden from the list).
    - Obsolete base CV: hard-delete (row + PDF) — the active base is kept.
    - Active base CV: the previous version (if any) is promoted to active so
      the user never loses an active base, then the CV is hard-deleted.
    """
    record = await get_cv(db, user_id, cv_id)

    if record.cv_type == "base":
        if record.base_status == "obsolete":
            await _hard_delete_base(db, record)
        else:
            # Deleting the active base: promote the previous version first.
            previous = await _user_obsolete_base_cv(db, user_id)
            if previous is not None:
                previous.base_status = "active"
            await _hard_delete_base(db, record)
        await db.commit()
        return

    _remove_pdf_file(record)
    record.is_deleted = True
    record.deleted_at = datetime.now(UTC)
    await db.commit()


async def count_recent_cvs(
    db: AsyncSession,
    user_id: str,
    window_minutes: int = 60,
) -> int:
    """Count CVs generated by a user within a rolling window (rate limiting)."""
    cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
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
    return resolve_existing("cv", record.pdf_path)


async def recompile_pdf_sync(db: AsyncSession, record: GeneratedCV) -> Path:
    """Recompile PDF on-demand from record.cv_json if disk file was wiped (Render Free ephemeral disk)."""
    abs_pdf_path, pdf_path = new_output_path("cv", record.user_id, f"{record.id}.pdf")
    async with TYPST_SEMAPHORE:
        await asyncio.to_thread(compile_cv, record.cv_json, output=abs_pdf_path)
    record.pdf_path = pdf_path
    await db.commit()
    return abs_pdf_path


def build_pdf_url(record: GeneratedCV) -> str | None:
    """Build the download URL for a CV.

    When R2 is configured, returns a signed URL directly.
    Otherwise, returns the backend download endpoint URL.
    """
    if not record.pdf_path:
        return None
    if r2_storage._r2_configured():
        return r2_storage.generate_signed_url(record.pdf_path)
    # Fallback: backend download endpoint (local disk)
    base = get_settings().base_url.rstrip("/")
    return f"{base}/api/v1/cv/{record.id}/download"
