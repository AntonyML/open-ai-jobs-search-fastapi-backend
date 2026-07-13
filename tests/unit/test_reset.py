"""Tests for the reset service."""

from __future__ import annotations

import os
from unittest.mock import PropertyMock, patch
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import Settings
from app.db.models import Base, CandidateProfile, User, BehavioralProfile, StarExample
from app.exceptions import ConfirmationRequiredError, NotFoundError
from app.services import reset


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Create user
        session.add(User(id="u1", email="t@t.com", hashed_password="x", full_name="T"))
        await session.commit()
        
        # Create profile, behavioral, star examples
        profile = CandidateProfile(id="p1", user_id="u1", full_name="John Doe")
        session.add(profile)
        await session.commit()
        
        bp = BehavioralProfile(id="b1", candidate_id="p1", profile_type="Driver")
        star = StarExample(id="s1", candidate_id="p1", title="Optimized DB", situation="Slow queries", task="Speed up", action="Added index", result="Faster")
        session.add(bp)
        session.add(star)
        await session.commit()
        
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_reset_profile_requires_confirmation(db_session):
    with pytest.raises(ConfirmationRequiredError) as exc_info:
        await reset.execute_reset(db_session, "u1", scope="profile")
        
    assert "Profile reset will clear" in exc_info.value.message
    assert "ConfirmationRequiredError" in exc_info.typename


@pytest.mark.asyncio
async def test_reset_profile_success(db_session):
    # Execute reset with confirmation
    result = await reset.execute_reset(db_session, "u1", scope="profile", confirm="RESET")
    
    assert result["status"] == "success"
    assert "Database: CandidateProfile, BehavioralProfile, STAR Examples" in result["cleared"]
    
    # Verify records are deleted
    p_res = await db_session.execute(select(CandidateProfile).where(CandidateProfile.user_id == "u1"))
    assert p_res.scalar_one_or_none() is None
    
    bp_res = await db_session.execute(select(BehavioralProfile).where(BehavioralProfile.candidate_id == "p1"))
    assert bp_res.scalar_one_or_none() is None

    star_res = await db_session.execute(select(StarExample).where(StarExample.candidate_id == "p1"))
    assert not star_res.scalars().all()


@pytest.mark.asyncio
async def test_reset_documents_success(db_session, tmp_path):
    # Setup temp documents directory
    doc_dir = tmp_path / "documents"
    cv_dir = doc_dir / "cv"
    cv_dir.mkdir(parents=True)
    
    # Write some files
    cv_file = cv_dir / "my_cv.pdf"
    cv_file.write_bytes(b"pdf contents")
    readme_file = cv_dir / "README.md"
    readme_file.write_text("don't delete me", encoding="utf-8")
    
    with patch("app.services.reset._get_documents_dir", return_value=doc_dir):
        # 1. Initiate reset -> raises confirmation error
        with pytest.raises(ConfirmationRequiredError):
            await reset.execute_reset(db_session, "u1", scope="documents")
            
        # 2. Confirm reset
        result = await reset.execute_reset(db_session, "u1", scope="documents", confirm="RESET")
        
    assert result["status"] == "success"
    
    # Verify my_cv.pdf was deleted, but README.md is still there
    assert not cv_file.exists()
    assert readme_file.exists()


@pytest.mark.asyncio
async def test_reset_all_success(db_session, tmp_path):
    doc_dir = tmp_path / "documents"
    cv_dir = doc_dir / "cv"
    cv_dir.mkdir(parents=True)
    cv_file = cv_dir / "my_cv.pdf"
    cv_file.write_bytes(b"pdf contents")
    
    with patch("app.services.reset._get_documents_dir", return_value=doc_dir):
        result = await reset.execute_reset(db_session, "u1", scope="all", confirm="RESET")
        
    assert result["status"] == "success"
    assert not cv_file.exists()
    
    p_res = await db_session.execute(select(CandidateProfile).where(CandidateProfile.user_id == "u1"))
    assert p_res.scalar_one_or_none() is None
