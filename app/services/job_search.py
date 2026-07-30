"""Job search service — reads from ingested_jobs, triggers microservice if needed."""

import logging

import httpx
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from app.core.settings import get_settings
from app.db.models import IngestedJob, IngestJob
from app.schemas.jobs import (
    JobSearchRequest,
    JobSearchResponse,
    JobOut,
    IngestStatusResponse,
)

logger = logging.getLogger(__name__)

MIN_RESULTS_THRESHOLD = 5


async def search_jobs(
    db: AsyncSession,
    req: JobSearchRequest,
    user: dict,
) -> JobSearchResponse:
    # ── DEBUG FASE 1.3: Ver qué keywords llegan y cuántos jobs hay ──
    logger.warning("[FASE 1.3 DEBUG] === SEARCH REQUEST ===")
    logger.warning("[FASE 1.3 DEBUG] keywords_raw='%s'", req.keywords)
    logger.warning("[FASE 1.3 DEBUG] location='%s'", req.location)
    logger.warning("[FASE 1.3 DEBUG] limit=%s", req.limit)

    query = select(IngestedJob).where(
        IngestedJob.expires_at > datetime.now(timezone.utc)
    )

    if req.keywords:
        terms = [t.strip() for t in req.keywords.split() if len(t.strip()) > 2]
        if terms:
            conditions = []
            for term in terms:
                pattern = f"%{term}%"
                conditions.append(IngestedJob.title.ilike(pattern))
                conditions.append(IngestedJob.description.ilike(pattern))
            query = query.where(or_(*conditions))

    if req.location:
        query = query.where(IngestedJob.location.ilike(f"%{req.location}%"))

    query = query.order_by(IngestedJob.ingested_at.desc()).limit(req.limit)

    # Log the compiled SQL
    compiled = query.compile(compile_kwargs={"literal_binds": True})
    logger.warning("[FASE 1.3 DEBUG] SQL=%s", compiled.string)

    result = await db.execute(query)
    jobs = result.scalars().all()

    logger.warning("[FASE 1.3 DEBUG] results_count=%s", len(jobs))
    logger.warning("[FASE 1.3 DEBUG] MIN_RESULTS_THRESHOLD=%s", MIN_RESULTS_THRESHOLD)

    if len(jobs) >= MIN_RESULTS_THRESHOLD:
        logger.warning("[FASE 1.3 DEBUG] Returning fresh=True (enough results)")
        return JobSearchResponse(
            jobs=[JobOut.model_validate(j) for j in jobs],
            count=len(jobs),
            fresh=True,
        )

    # Not enough results — trigger ingest
    category_id = _infer_category(req.keywords, req.location)
    logger.warning("[FASE 1.3 DEBUG] Not enough results. Triggering ingest for category=%s", category_id)

    ingest_job_id = await trigger_ingest(
        category_id=category_id,
        keywords=req.keywords,
    )
    logger.warning("[FASE 1.3 DEBUG] ingest_job_id=%s", ingest_job_id)

    return JobSearchResponse(
        jobs=[JobOut.model_validate(j) for j in jobs],
        count=len(jobs),
        fresh=False,
        ingest_job_id=ingest_job_id,
        message="Buscando m\u00e1s trabajos. Consulta el estado en unos segundos.",
    )


async def trigger_ingest(category_id: str, keywords: str) -> str | None:
    """Trigger a job ingest via the microservice.

    Returns the ingest_job_id on success, or None if the microservice
    is unavailable (5xx). 4xx errors still raise immediately.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.ingest_service_url}/api/v1/ingest",
                json={"category_id": category_id, "keywords": keywords},
            )
    except httpx.TimeoutException:
        logger.warning("[INGEST] Microservice timed out after 10s")
        return None
    except httpx.RequestError as e:
        logger.warning("[INGEST] Microservice unreachable: %s", e)
        return None

    if resp.status_code >= 500:
        logger.warning(
            "[INGEST] Microservice returned %s for category=%s",
            resp.status_code, category_id,
        )
        return None

    # 4xx still raise — these are client errors
    resp.raise_for_status()
    data = resp.json()
    return data["ingest_job_id"]


async def get_ingest_status(
    db: AsyncSession, ingest_job_id: str
) -> IngestStatusResponse:
    job = await db.get(IngestJob, ingest_job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingest job not found")

    return IngestStatusResponse(
        status=job.status,
        result_count=job.result_count,
        error=job.error,
    )


def _infer_category(keywords: str, location: str | None) -> str:
    kw = keywords.lower()
    loc = (location or "").lower()

    # STEM Costa Rica
    if "costa rica" in loc or " cr" in loc or "san jos" in loc or "heredia" in loc or "alajuela" in loc:
        return "stem_cr"

    # Dinamarca
    if "denmark" in loc or "danmark" in loc or "copenhagen" in loc or "københavn" in loc:
        return "stem_dk"

    # Remoto LATAM
    if "remoto" in loc or "remote" in loc or "latam" in loc or "cualquier país" in loc:
        return "latam_remote"

    # Freelance keywords
    if any(w in kw for w in ["freelance", "freelancer", "contractor", "project"]):
        return "freelance_intl"

    # Work from home keywords
    if any(w in kw for w in ["work from home", "home office", "data entry", "virtual assistant"]):
        return "from_work_home"

    # Default: stem_cr (Costa Rica es el mercado principal)
    return "stem_cr"
