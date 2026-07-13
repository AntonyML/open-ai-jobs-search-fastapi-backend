"""Tests for the salary service — async wrapper + copied salary_lookup module.

Verifies that the imported module from the source repo still works from
its new location.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def test_salary_lookup_module_imports():
    """The copied salary_lookup.py module imports cleanly."""
    from app.services.salary import salary_lookup

    assert hasattr(salary_lookup, "load_data")
    assert hasattr(salary_lookup, "match_score")
    assert hasattr(salary_lookup, "search_company")
    assert hasattr(salary_lookup, "format_entry")
    assert hasattr(salary_lookup, "normalize")


def test_match_score_exact():
    """match_score returns 100 for an exact match."""
    from app.services.salary.salary_lookup import match_score

    assert match_score("Novo Nordisk", "Novo Nordisk") == 100


def test_match_score_case_insensitive():
    """match_score is case-insensitive."""
    from app.services.salary.salary_lookup import match_score

    assert match_score("NOVO NORDISK", "Novo Nordisk") == 100


def test_normalize_strips_legal_suffixes():
    """normalize removes A/S, ApS, etc."""
    from app.services.salary.salary_lookup import normalize

    assert normalize("Mærsk A/S") == normalize("Mærsk")


def test_convert_salary_excel_imports():
    """The copied convert_salary_excel.py module imports cleanly."""
    from app.services.salary.tools import convert_salary_excel

    assert hasattr(convert_salary_excel, "parse_sheet")
    assert hasattr(convert_salary_excel, "detect_column_type")
    assert hasattr(convert_salary_excel, "header_matches")


def test_detect_column_type_index():
    """detect_column_type recognises index headers."""
    from app.services.salary.tools import convert_salary_excel

    assert convert_salary_excel.detect_column_type("Index") == "index"
    assert convert_salary_excel.detect_column_type("Salary Index") == "index"


def test_detect_column_type_count():
    """detect_column_type recognises count headers."""
    from app.services.salary.tools import convert_salary_excel

    assert convert_salary_excel.detect_column_type("Count") == "count"
    assert convert_salary_excel.detect_column_type("Antal medarbejdere") == "count"


@pytest.mark.asyncio
async def test_lookup_company_missing_data_file():
    """lookup_company raises NotFoundError when salary_data.json is missing."""
    from app.exceptions import NotFoundError
    from app.services.salary.service import lookup_company

    with patch("app.services.salary.service.DATA_FILE", Path("/nonexistent/salary_data.json")):
        with pytest.raises(NotFoundError):
            await lookup_company("Acme")


@pytest.mark.asyncio
async def test_lookup_company_with_temp_data():
    """lookup_company returns a match when salary_data.json exists."""
    sample = {
        "metadata": {"source": "test", "index_baseline": 100, "index_label": "Index", "baseline_description": "test"},
        "companies": [
            {
                "company": "Acme Corp",
                "city": "Copenhagen",
                "categories": {"all_employees": {"count": 100, "index": 105.5}},
            }
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(sample, f)
        tmp_path = Path(f.name)

    try:
        with patch("app.services.salary.service.DATA_FILE", tmp_path):
            from app.services.salary.service import lookup_company

            result = await lookup_company("Acme")
            assert result["company"] == "Acme Corp"
            assert result["city"] == "Copenhagen"
    finally:
        tmp_path.unlink()


@pytest.mark.asyncio
async def test_lookup_company_no_match_raises():
    """lookup_company raises NotFoundError when no match above threshold."""
    sample = {
        "metadata": {"source": "test", "index_baseline": 100, "index_label": "Index", "baseline_description": "test"},
        "companies": [
            {
                "company": "Acme Corp",
                "city": "Copenhagen",
                "categories": {"all_employees": {"count": 100, "index": 105.5}},
            }
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(sample, f)
        tmp_path = Path(f.name)

    try:
        from app.exceptions import NotFoundError
        from app.services.salary.service import lookup_company

        with patch("app.services.salary.service.DATA_FILE", tmp_path):
            with pytest.raises(NotFoundError):
                await lookup_company("NonexistentCompanyXYZ")
    finally:
        tmp_path.unlink()