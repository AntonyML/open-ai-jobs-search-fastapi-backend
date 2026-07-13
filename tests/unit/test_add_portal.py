"""Tests for the add_portal service.

Strategy:
- tmp_path (pytest built-in) para filesystem real — sin mocking de Path
- Parchea settings.scrapers_dir al tmp_path
- Mockea LLM y asyncio.create_subprocess_exec
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, User
from app.exceptions import LLMError, NotFoundError
from app.schemas.add_portal import (
    AddPortalRequest,
    PortalInvestigationLLMOutput,
    PortalSkillGenerationLLMOutput,
)
from app.services import add_portal


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(User(id="u1", email="t@t.com", hashed_password="x", full_name="T"))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def req():
    return AddPortalRequest(
        portal_url="https://seek.com.au",
        skill_name="seek-search",
        market_and_language="Australia/English",
        test_query="software engineer",
    )


# ── Mock helpers ─────────────────────────────────────────────────────


def _investigation():
    return PortalInvestigationLLMOutput(
        search_endpoint="https://seek.com.au/api/search",
        query_param="keywords",
        result_fields={"id": "$.id", "title": "$.title"},
    )


def _generation():
    return PortalSkillGenerationLLMOutput(
        cli_ts="// cli",
        search_ts="// search",
        detail_ts="// detail",
        helpers_ts="// helpers",
        package_json=json.dumps({"name": "seek-search-cli", "description": "https://seek.com.au"}),
        tsconfig_json=json.dumps({"compilerOptions": {}}),
        readme_md="# seek",
        test_helpers_ts="// test helpers",
    )


def _proc(code: int, stdout: bytes = b"", stderr: bytes = b""):
    p = AsyncMock()
    p.returncode = code
    p.communicate = AsyncMock(return_value=(stdout, stderr))
    return p


_OK_STDOUT = json.dumps({"results": [{"id": "1", "title": "Dev"}]}).encode()


# ── execute_add_portal ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success(db_session, req, tmp_path):
    # Three subprocess calls: bun install, bun typecheck, bun run search
    procs = [_proc(0), _proc(0), _proc(0, _OK_STDOUT)]
    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        with patch("app.services.add_portal.llm_completion_structured",
                   side_effect=[_investigation(), _generation()]):
            with patch("asyncio.create_subprocess_exec", side_effect=procs):
                result = await add_portal.execute_add_portal(db_session, "u1", req)

    assert result["skill_name"] == "seek-search"
    assert result["status"] in ("completed", "completed_with_warnings")
    assert "created_at" in result
    # Files were actually written
    assert (tmp_path / "seek-search" / "cli" / "package.json").exists()


@pytest.mark.asyncio
async def test_skill_already_exists(db_session, req, tmp_path):
    (tmp_path / "seek-search").mkdir()
    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        with pytest.raises(ValueError, match="already exists"):
            await add_portal.execute_add_portal(db_session, "u1", req)


@pytest.mark.asyncio
async def test_user_not_found(db_session, req, tmp_path):
    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        with pytest.raises(NotFoundError):
            await add_portal.execute_add_portal(db_session, "ghost", req)


@pytest.mark.asyncio
async def test_llm_investigation_error(db_session, req, tmp_path):
    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        with patch("app.services.add_portal.llm_completion_structured",
                   side_effect=Exception("timeout")):
            with pytest.raises(LLMError, match="investigation"):
                await add_portal.execute_add_portal(db_session, "u1", req)


@pytest.mark.asyncio
async def test_llm_generation_error(db_session, req, tmp_path):
    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        with patch("app.services.add_portal.llm_completion_structured",
                   side_effect=[_investigation(), Exception("ctx overflow")]):
            with pytest.raises(LLMError, match="generation"):
                await add_portal.execute_add_portal(db_session, "u1", req)


@pytest.mark.asyncio
async def test_bun_fails_returns_warning(db_session, req, tmp_path):
    # bun install fails → completed_with_warnings, not an exception
    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        with patch("app.services.add_portal.llm_completion_structured",
                   side_effect=[_investigation(), _generation()]):
            with patch("asyncio.create_subprocess_exec",
                       return_value=_proc(1, b"", b"bun not found")):
                result = await add_portal.execute_add_portal(db_session, "u1", req)

    assert result["status"] == "completed_with_warnings"
    assert result["test_result"]["success"] is False


# ── get_portal_skill ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_skill_not_found(db_session, tmp_path):
    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        with pytest.raises(NotFoundError):
            await add_portal.get_portal_skill(db_session, "no-such-skill", "u1")


@pytest.mark.asyncio
async def test_get_skill_found(db_session, tmp_path):
    skill_dir = tmp_path / "seek-search" / "cli"
    skill_dir.mkdir(parents=True)
    (skill_dir / "package.json").write_text(
        json.dumps({"name": "seek-search-cli", "description": "https://seek.com.au"}),
        encoding="utf-8",
    )
    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        result = await add_portal.get_portal_skill(db_session, "seek-search", "u1")

    assert result["skill_name"] == "seek-search"
    assert result["portal_url"] == "https://seek.com.au"
    assert result["status"] == "installed"


# ── list_portal_skills ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_empty_when_no_dir(db_session, tmp_path):
    missing = tmp_path / "scrapers"  # doesn't exist
    with patch.object(add_portal.settings, "scrapers_dir", str(missing)):
        result = await add_portal.list_portal_skills(db_session, "u1")
    assert result == []


@pytest.mark.asyncio
async def test_list_returns_installed_skills(db_session, tmp_path):
    for name, url in [("seek-search", "https://seek.com.au"), ("indeed-search", "https://indeed.com")]:
        pkg_dir = tmp_path / name / "cli"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text(
            json.dumps({"description": url}), encoding="utf-8"
        )

    with patch.object(add_portal.settings, "scrapers_dir", str(tmp_path)):
        result = await add_portal.list_portal_skills(db_session, "u1")

    assert len(result) == 2
    names = {r["skill_name"] for r in result}
    assert "seek-search" in names
    assert "indeed-search" in names
