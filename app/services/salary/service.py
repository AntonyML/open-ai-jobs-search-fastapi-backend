"""Async wrapper around salary_lookup for FastAPI services.

Supports two sources of salary data:
1. Per-user data stored in the UserSalaryData DB table (takes priority)
2. Global salary_data.json file (fallback)

The underlying salary_lookup.py is synchronous stdlib code.  This wrapper
runs it in a thread executor so it doesn't block the event loop.
"""

from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserSalaryData
from app.exceptions import NotFoundError
from app.schemas.salary import SalaryBenchmark

# Re-export the synchronous functions for callers that want them.
from app.services.salary.salary_lookup import (  # noqa: F401
    format_entry,
    load_data,
    match_score,
    normalize,
    search_company,
)

DATA_FILE = Path(__file__).parent / "salary_data.json"


# ── DB-backed per-user lookup ───────────────────────────────────────


async def get_user_salary_data(db: AsyncSession, user_id: str) -> dict[str, Any] | None:
    """Get the user's uploaded salary data from the DB.

    Returns a dict with "companies", "metadata" keys, or None if no data.
    """
    result = await db.execute(select(UserSalaryData).where(UserSalaryData.user_id == user_id))
    record = result.scalar_one_or_none()
    if record is None or not record.companies:
        return None

    return {
        "companies": record.companies,
        "metadata": record.data_metadata or {},
    }


async def save_user_salary_data(
    db: AsyncSession,
    user_id: str,
    companies: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
    source: str = "json_upload",
) -> int:
    """Save or update the user's salary data in the DB.

    Args:
        db: Database session.
        user_id: The authenticated user's ID.
        companies: Array of company salary entries.
        metadata: Optional metadata dict.
        source: Data source identifier.

    Returns:
        Number of companies saved.
    """
    from app.db.models import UserSalaryData

    # Upsert: find existing or create new
    result = await db.execute(select(UserSalaryData).where(UserSalaryData.user_id == user_id))
    record = result.scalar_one_or_none()

    if record is None:
        record = UserSalaryData(
            user_id=user_id,
            source=source,
            companies=companies,
            data_metadata=metadata or {},
            company_count=len(companies),
        )
        db.add(record)
    else:
        record.source = source
        record.companies = companies
        record.data_metadata = metadata or {}
        record.company_count = len(companies)

    await db.flush()
    return len(companies)


async def lookup_company_for_user(
    db: AsyncSession,
    user_id: str,
    company_name: str,
    city: str | None = None,
    salary_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Look up a company in the user's salary data.

    Checks the user's DB data first, then falls back to the global file.

    Args:
        db: Database session.
        user_id: The authenticated user's ID.
        company_name: Company to search for (fuzzy match).
        city: Optional city filter.
        salary_data: Pre-loaded salary data to avoid extra DB query.

    Returns:
        Best matching company entry with _match_score, or None if not found.
    """
    # 1. Try user's DB data
    if salary_data is None:
        user_data = await get_user_salary_data(db, user_id)
    else:
        user_data = salary_data
    if user_data is not None:
        matches = search_company(user_data, company_name, city=city)
        matches = [m for m in matches if m.get("_match_score", 0) >= 70.0]
        if matches:
            return matches[0]

    # 2. Fall back to global file
    try:
        return await lookup_company(company_name, city=city)
    except NotFoundError:
        return None


# ── Benchmarking ─────────────────────────────────────────────────────


async def benchmark_job(
    db: AsyncSession,
    user_id: str,
    company_name: str,
    job_title: str | None = None,
    job_location: str | None = None,
    salary_data: dict[str, Any] | None = None,
) -> SalaryBenchmark | None:
    """Create a salary benchmark for a job posting.

    Looks up the company in salary data and computes benchmark values
    that can be displayed alongside the rank evaluation.

    NOTE on index values vs. actual salary amounts:
    Salary data files contain *index values* relative to a baseline
    (e.g. 100 = market median, 105 = 5% above market).  These are NOT
    actual salary amounts in currency units.  The delta_pct field
    expresses the percentage difference from market baseline.

    Args:
        db: Database session.
        user_id: The authenticated user's ID.
        company_name: Company name from the job posting.
        job_title: Optional job title (for role-specific benchmarks).
        job_location: Optional job location (for location-specific benchmarks).

    Returns:
        SalaryBenchmark with index values and delta_pct, or None if no salary
        data is available for this company.
    """
    if not company_name:
        return None

    # Try user data first, then global file
    match = await lookup_company_for_user(db, user_id, company_name, city=job_location, salary_data=salary_data)
    if match is None:
        return None

    # Extract category data
    categories = match.get("categories", {})
    all_employees = categories.get("all_employees", {})
    index_value = all_employees.get("index")

    # Compute salary estimate from categories (if available)
    # The categories have 'index' values relative to baseline (100.0)
    salary_estimate = None
    market_median = None
    salary_delta_pct = None

    if index_value is not None and isinstance(index_value, int | float):
        # Index is relative to baseline 100.0 (e.g. 105 = 5% above median)
        salary_estimate = index_value
        market_median = 100.0  # baseline index
        salary_delta_pct = index_value - 100.0

    return SalaryBenchmark(
        company_name=match.get("company", company_name),
        match_confidence=match.get("_match_score", 0),
        city=match.get("city"),
        salary_estimate=salary_estimate,
        market_median=market_median,
        salary_delta_pct=salary_delta_pct,
        index_value=index_value,
        data_source="user_upload",
    )


# ── Legacy file-based lookup (unchanged) ────────────────────────────


async def lookup_company(company_name: str, city: str | None = None) -> dict[str, Any]:
    """Look up a company in salary_data.json asynchronously.

    Args:
        company_name: Company to search for (fuzzy match).
        city: Optional city filter.

    Returns:
        Dict with company, city, and matched categories.

    Raises:
        NotFoundError: No match found above the match-score threshold.
    """
    loop = asyncio.get_running_loop()
    func = partial(_sync_lookup, company_name, city)
    return await loop.run_in_executor(None, func)


def _sync_lookup(company_name: str, city: str | None) -> dict[str, Any]:
    """Synchronous implementation — runs in a thread executor."""
    if not DATA_FILE.exists():
        raise NotFoundError(
            "salary_data.json not found. "
            "Drop your salary data file in app/services/salary/ "
            "or convert from Excel via tools/convert_salary_excel.py."
        )

    data = load_data()
    matches = search_company(data, company_name, city=city)

    # Apply threshold and limit
    matches = [m for m in matches if m.get("_match_score", 0) >= 70.0]
    matches = matches[:1]

    if not matches:
        raise NotFoundError(f"No salary data found for '{company_name}'")

    return matches[0]


async def list_companies() -> list[dict[str, Any]]:
    """Return all companies in salary_data.json (no fuzzy matching).

    Raises:
        NotFoundError: salary_data.json is missing or empty.
    """
    if not DATA_FILE.exists():
        raise NotFoundError("salary_data.json not found")

    data = load_data()
    companies = data.get("companies", [])
    if not companies:
        raise NotFoundError("No companies in salary_data.json")
    return companies


async def get_metadata() -> dict[str, Any]:
    """Return the metadata block from salary_data.json."""
    if not DATA_FILE.exists():
        raise NotFoundError("salary_data.json not found")
    data = load_data()
    return data.get("metadata", {})
