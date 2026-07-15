"""Tests for the salary service — async wrapper, per-user DB storage, and integration.

Verifies that the salary service works correctly with:
- Global salary_data.json file-based lookup
- Per-user DB-backed salary lookup
- Salary benchmark integration with the rank flow
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, User
from app.schemas.salary import SalaryBenchmark


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        # Create a test user
        user = User(id="test-user-1", email="test@example.com", hashed_password="fakehash")
        session.add(user)
        await session.commit()
        yield session

    await engine.dispose()


# ── Basic module tests ──────────────────────────────────────────────


def test_salary_lookup_module_imports():
    """The salary_lookup.py module imports cleanly."""
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
    """The convert_salary_excel.py module imports cleanly."""
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


# ── Async wrapper tests ──────────────────────────────────────


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
            with patch("app.services.salary.salary_lookup.DATA_FILE", tmp_path):
                from app.services.salary.service import lookup_company

                result = await lookup_company("Acme")
                assert result["company"] == "Acme Corp"
                assert result["city"] == "Copenhagen"
                assert result["_match_score"] >= 70
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


# ── Per-user DB-backed tests (use DB session) ─────────────────


@pytest.mark.asyncio
async def test_get_user_salary_data_no_data(db_session):
    """get_user_salary_data returns None when user has no data."""
    from app.services.salary.service import get_user_salary_data

    result = await get_user_salary_data(db_session, "nonexistent-user")
    assert result is None


@pytest.mark.asyncio
async def test_save_and_get_user_salary_data(db_session):
    """Saving and retrieving user salary data works correctly."""
    from app.services.salary.service import get_user_salary_data, save_user_salary_data

    companies = [
        {"company": "Acme Corp", "city": "Copenhagen", "categories": {"all_employees": {"count": 100, "index": 105.5}}},
        {"company": "Beta Inc", "city": "Aarhus", "categories": {"all_employees": {"count": 50, "index": 98.0}}},
    ]
    metadata = {"source": "test", "index_baseline": 100}

    count = await save_user_salary_data(
        db_session, user_id="test-user-1",
        companies=companies, metadata=metadata,
        source="json_upload",
    )
    assert count == 2

    result = await get_user_salary_data(db_session, "test-user-1")
    assert result is not None
    assert len(result["companies"]) == 2
    assert result["metadata"]["source"] == "test"

    # Verify it returns None for a different user
    result2 = await get_user_salary_data(db_session, "test-user-2")
    assert result2 is None


@pytest.mark.asyncio
async def test_save_user_salary_data_upsert(db_session):
    """Saving twice for the same user updates the data (upsert)."""
    from app.services.salary.service import get_user_salary_data, save_user_salary_data

    companies_1 = [{"company": "Old Corp", "city": "Copenhagen", "categories": {}}]
    await save_user_salary_data(db_session, "test-user-1", companies=companies_1)

    # Save again with different data (same user)
    companies_2 = [{"company": "New Corp", "city": "Aarhus", "categories": {}}]
    await save_user_salary_data(db_session, "test-user-1", companies=companies_2)

    result = await get_user_salary_data(db_session, "test-user-1")
    assert result["companies"][0]["company"] == "New Corp"
    assert len(result["companies"]) == 1


@pytest.mark.asyncio
async def test_lookup_company_for_user_uses_db_first(db_session):
    """lookup_company_for_user checks user DB before falling back to file."""
    from app.services.salary.service import save_user_salary_data, lookup_company_for_user

    # Save user-specific data
    companies = [{"company": "CustomCo", "city": "Copenhagen", "categories": {"all_employees": {"count": 10, "index": 110.0}}}]
    await save_user_salary_data(db_session, "test-user-1", companies=companies)

    # Mock the file-based lookup to raise (so we know DB is used)
    with patch("app.services.salary.service.lookup_company", side_effect=Exception("Should not reach file")):
        result = await lookup_company_for_user(db_session, "test-user-1", "CustomCo")
        assert result is not None
        assert result["company"] == "CustomCo"
        assert result["_match_score"] >= 70


@pytest.mark.asyncio
async def test_lookup_company_for_user_falls_back_to_file(db_session):
    """lookup_company_for_user falls back to file when user has no DB data."""
    from app.services.salary.service import lookup_company_for_user

    sample = {
        "metadata": {"source": "test", "index_baseline": 100, "index_label": "Index", "baseline_description": "test"},
        "companies": [{"company": "FileCo", "city": "Copenhagen", "categories": {"all_employees": {"count": 100, "index": 105.5}}}],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(sample, f)
        tmp_path = Path(f.name)

    try:
        with patch("app.services.salary.service.DATA_FILE", tmp_path):
            with patch("app.services.salary.salary_lookup.DATA_FILE", tmp_path):
                result = await lookup_company_for_user(db_session, "test-user-1", "FileCo")
                assert result is not None
                assert result["company"] == "FileCo"
    finally:
        tmp_path.unlink()


@pytest.mark.asyncio
async def test_lookup_company_for_user_not_found(db_session):
    """lookup_company_for_user returns None when company is not in any source."""
    from app.exceptions import NotFoundError
    from app.services.salary.service import lookup_company_for_user

    with patch("app.services.salary.service.lookup_company", side_effect=NotFoundError("No file")):
        with patch("app.services.salary.salary_lookup.DATA_FILE", Path("/nonexistent")):
            result = await lookup_company_for_user(db_session, "test-user-1", "NonexistentXYZ")
            assert result is None


@pytest.mark.asyncio
async def test_benchmark_job_no_company(db_session):
    """benchmark_job returns None when company_name is empty."""
    from app.services.salary.service import benchmark_job

    result = await benchmark_job(db_session, "test-user-1", company_name="")
    assert result is None


@pytest.mark.asyncio
async def test_benchmark_job_with_data(db_session):
    """benchmark_job returns salary benchmark when company is found."""
    from app.services.salary.service import benchmark_job, save_user_salary_data

    companies = [{
        "company": "Acme Corp", "city": "Copenhagen",
        "categories": {"all_employees": {"count": 100, "index": 105.5}},
    }]
    await save_user_salary_data(db_session, "test-user-1", companies=companies)

    result = await benchmark_job(db_session, "test-user-1", company_name="Acme Corp")
    assert result is not None
    assert isinstance(result, SalaryBenchmark)
    assert result.company_name == "Acme Corp"
    assert result.index_value == 105.5
    assert result.salary_delta_pct == 5.5  # 105.5 - 100.0


@pytest.mark.asyncio
async def test_benchmark_job_not_found(db_session):
    """benchmark_job returns None when company is not in user's data."""
    from app.services.salary.service import benchmark_job

    result = await benchmark_job(db_session, "test-user-1", company_name="UnknownCo Inc")
    assert result is None


# ── Schema validation tests ──────────────────────────────────


def test_salary_company_entry_schema():
    """SalaryCompanyEntry accepts valid data with extra fields."""
    from app.schemas.salary import SalaryCompanyEntry

    entry = SalaryCompanyEntry(
        company="Acme Corp",
        city="Copenhagen",
        categories={"all_employees": {"count": 100, "index": 105.5}},
    )
    assert entry.company == "Acme Corp"
    assert entry.city == "Copenhagen"


def test_salary_data_upload_schema():
    """SalaryDataUpload validates correctly."""
    from app.schemas.salary import SalaryCompanyEntry, SalaryDataUpload, SalaryMetadata

    upload = SalaryDataUpload(
        companies=[
            SalaryCompanyEntry(company="Acme Corp", categories={"all": {"count": 100, "index": 105.0}}),
        ],
        metadata=SalaryMetadata(source="test", index_baseline=100),
    )
    assert len(upload.companies) == 1
    assert upload.metadata.source == "test"


def test_salary_benchmark_schema():
    """SalaryBenchmark can be constructed with delta > 0 (above market)."""
    from app.schemas.salary import SalaryBenchmark

    bench = SalaryBenchmark(
        company_name="Acme Corp",
        match_confidence=95,
        salary_estimate=110.0,
        market_median=100.0,
        salary_delta_pct=10.0,
        index_value=110.0,
    )
    assert bench.salary_delta_pct == 10.0
    assert bench.match_confidence == 95


def test_salary_benchmark_negative_delta():
    """SalaryBenchmark can be constructed with delta < 0 (below market)."""
    from app.schemas.salary import SalaryBenchmark

    bench = SalaryBenchmark(
        company_name="LowPay Inc",
        match_confidence=80,
        salary_estimate=85.0,
        market_median=100.0,
        salary_delta_pct=-15.0,
    )
    assert bench.salary_delta_pct == -15.0
