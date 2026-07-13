"""Tests for the add_template service."""

from __future__ import annotations

from unittest.mock import AsyncMock, PropertyMock, patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import Settings
from app.db.models import Base, User
from app.exceptions import LatexCompileError, NotFoundError
from app.schemas.add_template import AddTemplateRequest, SwitchTemplateRequest
from app.services import add_template


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
    return AddTemplateRequest(
        name="awesome-cv",
        template_type="cv",
        latex_content="\\documentclass{article}\\begin{document}[YOUR_NAME]\\end{document}",
        engine="lualatex",
        fonts="lato",
        style_rules=["rule1"],
        page_limit=2,
        known_pitfalls="none",
    )


def _proc(code: int, stdout: bytes = b"", stderr: bytes = b""):
    p = AsyncMock()
    p.returncode = code
    p.communicate = AsyncMock(return_value=(stdout, stderr))
    return p


@pytest.mark.asyncio
async def test_add_template_success(db_session, req, tmp_path):
    # Two compile processes (we run latex twice)
    procs = [_proc(0), _proc(0)]
    
    with patch.object(Settings, "templates_dir", new_callable=PropertyMock, return_value=tmp_path):
        with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
            # We mock _get_pdf_page_count to return 1 page
            with patch("app.services.add_template._get_pdf_page_count", return_value=1):
                # We need to ensure the pdf file exists so that the service doesn't raise error
                def create_dummy_pdf(*args, **kwargs):
                    pdf_file = tmp_path / "cv" / "awesome-cv" / "_compile_test.pdf"
                    pdf_file.parent.mkdir(parents=True, exist_ok=True)
                    pdf_file.write_bytes(b"%PDF-1.4...")
                    return b"", b""
                
                procs[1].communicate = AsyncMock(side_effect=create_dummy_pdf)
                
                result = await add_template.execute_add_template(db_session, "u1", req)

    assert result["name"] == "awesome-cv"
    assert result["active"] is True
    assert (tmp_path / "cv" / "awesome-cv" / "template.tex").exists()
    assert (tmp_path / "cv" / "awesome-cv" / "TEMPLATE.md").exists()


@pytest.mark.asyncio
async def test_add_template_compile_error(db_session, req, tmp_path):
    procs = [_proc(1, b"", b"missing package error")]
    
    with patch.object(Settings, "templates_dir", new_callable=PropertyMock, return_value=tmp_path):
        with patch("asyncio.create_subprocess_exec", side_effect=procs):
            with pytest.raises(LatexCompileError, match="Template test compilation failed"):
                await add_template.execute_add_template(db_session, "u1", req)


@pytest.mark.asyncio
async def test_add_template_user_not_found(db_session, req, tmp_path):
    with patch.object(Settings, "templates_dir", new_callable=PropertyMock, return_value=tmp_path):
        with pytest.raises(NotFoundError):
            await add_template.execute_add_template(db_session, "ghost", req)


@pytest.mark.asyncio
async def test_switch_template_to_default(db_session, tmp_path):
    switch_req = SwitchTemplateRequest(name="default", template_type="cv")
    
    with patch.object(Settings, "templates_dir", new_callable=PropertyMock, return_value=tmp_path):
        result = await add_template.execute_switch_template(db_session, "u1", switch_req)
        
    assert result["name"] == "default"
    assert "reverted" in result["message"]


@pytest.mark.asyncio
async def test_switch_template_not_found(db_session, tmp_path):
    switch_req = SwitchTemplateRequest(name="non-existent", template_type="cv")
    
    with patch.object(Settings, "templates_dir", new_callable=PropertyMock, return_value=tmp_path):
        with pytest.raises(NotFoundError):
            await add_template.execute_switch_template(db_session, "u1", switch_req)
