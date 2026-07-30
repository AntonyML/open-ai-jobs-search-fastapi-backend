"""Job search endpoint — reads from ingested_jobs, triggers microservice if needed."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.jobs import (
    JobSearchRequest,
    JobSearchResponse,
    IngestStatusResponse,
)
from app.services.job_search import search_jobs, get_ingest_status

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/search", response_model=JobSearchResponse)
async def search(
    req: JobSearchRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await search_jobs(db, req, user)


@router.get("/search/{ingest_job_id}/status", response_model=IngestStatusResponse)
async def ingest_status(
    ingest_job_id: str,
    db: AsyncSession = Depends(get_db),
):
    return await get_ingest_status(db, ingest_job_id)


# ── Debug endpoints (TEMPORALES — eliminar después del diagnóstico) ────────


@router.get("/debug/db-status")
async def debug_db_status(db: AsyncSession = Depends(get_db)):
    """TEMPORAL — Diagnóstico: muestra el estado de ingested_jobs en la BD.

    Abrir en el navegador: http://localhost:8000/api/v1/jobs/debug/db-status
    """
    total = (await db.execute(
        text("SELECT COUNT(*) FROM ingested_jobs")
    )).scalar()

    activos = (await db.execute(
        text("SELECT COUNT(*) FROM ingested_jobs WHERE expires_at > NOW()")
    )).scalar()

    expirados = (await db.execute(
        text("SELECT COUNT(*) FROM ingested_jobs WHERE expires_at <= NOW()")
    )).scalar()

    sample = []
    if total > 0:
        rows = (await db.execute(text(
            "SELECT title, location, category_id, expires_at, ingested_at "
            "FROM ingested_jobs ORDER BY ingested_at DESC LIMIT 5"
        ))).fetchall()
        sample = [
            {
                "title": r[0],
                "location": r[1],
                "category": r[2],
                "expires": str(r[3]),
                "ingested": str(r[4]),
            }
            for r in rows
        ]

    # Verificar location ILIKE 'Costa Rica'
    cr_count = (await db.execute(text(
        "SELECT COUNT(*) FROM ingested_jobs WHERE expires_at > NOW() "
        "AND location ILIKE '%Costa Rica%'"
    ))).scalar() if activos > 0 else 0

    # Distribución por categoría
    distro = []
    if activos > 0:
        cat_rows = (await db.execute(text(
            "SELECT category_id, COUNT(*) as cnt FROM ingested_jobs "
            "WHERE expires_at > NOW() GROUP BY category_id ORDER BY cnt DESC"
        ))).fetchall()
        distro = [{"category": r[0], "count": r[1]} for r in cat_rows]

    # Últimas ingestas
    ingest_rows = (await db.execute(text(
        "SELECT id, category_id, status, result_count, error, created_at, completed_at "
        "FROM ingest_jobs ORDER BY created_at DESC LIMIT 5"
    ))).fetchall()
    ingestas = [
        {
            "id": str(r[0])[:12] + "..",
            "category": r[1],
            "status": r[2],
            "result_count": r[3],
            "error": str(r[4] or "")[:80],
            "created_at": str(r[5]),
            "completed_at": str(r[6] or ""),
        }
        for r in ingest_rows
    ]

    return {
        "total": total,
        "activos": activos,
        "expirados": expirados,
        "coinciden_con_costa_rica": cr_count,
        "distribucion": distro,
        "sample": sample,
        "ultimas_ingestas": ingestas,
    }
