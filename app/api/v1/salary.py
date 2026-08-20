"""Salary router — endpoints for uploading and benchmarking salary data.

POST /api/v1/profile/salary-data  — Upload salary data (JSON)
GET  /api/v1/profile/salary-data  — Get user's salary data status
GET  /api/v1/rank/jobs/{job_id}/salary  — Get salary benchmark for a specific job
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db as _get_db
from app.schemas.salary import (
    SalaryCompanyEntry,
    SalaryDataStatus,
    SalaryDataUpload,
    SalaryUploadResponse,
)
from app.services.salary import service as salary_service

router = APIRouter(prefix="/profile", tags=["salary"])


@router.post("/salary-data", response_model=SalaryUploadResponse)
async def upload_salary_data(
    payload: SalaryDataUpload,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Upload salary data (JSON format).

    The data should match the salary_data.json schema:
    {
      "companies": [{"company": "...", "city": "...", "categories": {...}}],
      "metadata": {"source": "...", "index_baseline": 100, ...}
    }

    This data is stored per-user and takes priority over the global
    salary_data.json file during ranking.
    """
    companies_dicts = [c.model_dump() for c in payload.companies]
    metadata_dict = payload.metadata.model_dump()

    company_count = await salary_service.save_user_salary_data(
        db=db,
        user_id=user["sub"],
        companies=companies_dicts,
        metadata=metadata_dict,
        source="json_upload",
    )
    await db.commit()

    return SalaryUploadResponse(
        status="ok",
        company_count=company_count,
        message=f"Salary data saved with {company_count} companies.",
    )


@router.get("/salary-data", response_model=SalaryDataStatus)
async def get_salary_data_status(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get the status of the user's salary data.

    Returns whether data exists, how many companies, and the upload date.
    """
    from sqlalchemy import select

    from app.db.models import UserSalaryData

    result = await db.execute(select(UserSalaryData).where(UserSalaryData.user_id == user["sub"]))
    record = result.scalar_one_or_none()

    if record is None:
        return SalaryDataStatus(has_data=False)

    return SalaryDataStatus(
        has_data=True,
        company_count=record.company_count,
        source=record.source,
        uploaded_at=record.created_at,
        companies=[SalaryCompanyEntry(**c) for c in (record.companies or [])],
    )


@router.delete("/salary-data")
async def delete_salary_data(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete the user's salary data."""
    from sqlalchemy import delete

    from app.db.models import UserSalaryData

    await db.execute(delete(UserSalaryData).where(UserSalaryData.user_id == user["sub"]))
    await db.commit()
    return {"status": "deleted"}
