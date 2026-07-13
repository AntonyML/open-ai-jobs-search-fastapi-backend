"""Async wrapper around salary_lookup for FastAPI services.

The underlying salary_lookup.py is synchronous stdlib code.  This wrapper
runs it in a thread executor so it doesn't block the event loop.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from pathlib import Path
from typing import Any

from app.exceptions import NotFoundError

# Re-export the synchronous functions for callers that want them.
from app.services.salary.salary_lookup import (  # noqa: F401
    format_entry,
    match_score,
    normalize,
    search_company,
    load_data,
)

DATA_FILE = Path(__file__).parent / "salary_data.json"


async def lookup_company(
    company_name: str, city: str | None = None
) -> dict[str, Any]:
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
    matches = search_company(data, company_name, city=city, top_n=1, threshold=70.0)

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