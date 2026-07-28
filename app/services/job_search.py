"""Job search service — reads from ingested_jobs, triggers microservice if needed."""

import httpx
from fastapi import HTTPException
from sqlalchemy import select
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

MIN_RESULTS_THRESHOLD = 5


async def search_jobs(
    db: AsyncSession,
    req: JobSearchRequest,
    user: dict,
) -> JobSearchResponse:
    query = select(IngestedJob).where(
        IngestedJob.expires_at > datetime.now(timezone.utc)
    )

    if req.keywords:
        kw = f"%{req.keywords}%"
        query = query.where(
            IngestedJob.title.ilike(kw) | IngestedJob.description.ilike(kw)
        )

    if req.location:
        query = query.where(IngestedJob.location.ilike(f"%{req.location}%"))

    query = query.order_by(IngestedJob.ingested_at.desc()).limit(req.limit)

    result = await db.execute(query)
    jobs = result.scalars().all()

    if len(jobs) >= MIN_RESULTS_THRESHOLD:
        return JobSearchResponse(
            jobs=[JobOut.model_validate(j) for j in jobs],
            count=len(jobs),
            fresh=True,
        )

    ingest_job_id = await trigger_ingest(
        category_id=_infer_category(req.keywords, req.location),
        keywords=req.keywords,
    )

    return JobSearchResponse(
        jobs=[JobOut.model_validate(j) for j in jobs],
        count=len(jobs),
        fresh=False,
        ingest_job_id=ingest_job_id,
        message="Buscando m\u00e1s trabajos. Consulta el estado en unos segundos.",
    )


async def trigger_ingest(category_id: str, keywords: str) -> str:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.ingest_service_url}/api/v1/ingest",
            json={"category_id": category_id, "keywords": keywords},
        )
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

    if "costa rica" in loc or " cr" in loc or "san jos" in loc:
        return "stem_cr"
    if "denmark" in loc or "danmark" in loc or "copenhagen" in loc:
        return "stem_dk"
    if "remote" in loc or "remoto" in loc:
        return "stem_remote"

    return "stem_cr"
