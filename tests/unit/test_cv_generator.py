"""Tests for the CV generator service (FASE 1).

Uses an in-memory SQLite database and mocks the LLM + Typst compile steps
so no network or typst binary is required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, CandidateProfile, User
from app.exceptions import NotFoundError, ProfileIncompleteError, ProviderAuthError
from app.services import cv_generator

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """In-memory SQLite DB with a test user and candidate profile."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                id="test-user-id",
                email="test@example.com",
                hashed_password="fakehash",
                full_name="Test User",
            )
        )
        session.add(
            CandidateProfile(
                user_id="test-user-id",
                full_name="Jane Doe",
                location="Copenhagen, Denmark",
                email="jane@example.com",
                constraints="No relocation",
                education=[
                    {"degree": "MSc Computer Science", "institution": "DTU", "period": "2018-2020"}
                ],
                experience=[
                    {
                        "title": "Software Engineer",
                        "company": "Acme",
                        "start_date": "2020-01",
                        "end_date": "Present",
                        "bullets": ["Built X to improve Y"],
                    }
                ],
                skills={"programming_ml": [{"language": "Python", "proficiency": "advanced"}]},
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


SAMPLE_OUTPUT = {
    "cv": {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "experience": [
            {
                "title": "Software Engineer",
                "company": "Acme",
                "date_range": {"start": "2020-01", "end": "Present"},
                "bullets": ["Built X to improve Y"],
            }
        ],
        "profile_statement": "Results-driven engineer.",
    },
    "metadata": {"language": "en"},
}

SAMPLE_ANALYSIS = {
    "match_score": 78,
    "missing_keywords": ["Kubernetes"],
    "red_flags": ["Few senior years"],
    "adapted_experience": ["Lead the X bullet with scale"],
}


# ── Tests ───────────────────────────────────────────────────────────


PROVIDER_CFG = {"provider": "openai", "model": "gpt-4o", "api_key": "sk-test", "api_base": None}


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch("app.services.cv_generator.generate_base_cv_llm", new=AsyncMock(return_value=SAMPLE_OUTPUT))
@patch(
    "app.services.cv_generator.get_user_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_generate_base_cv_persists(db_session):
    record = await cv_generator.generate_base_cv(db_session, "test-user-id")

    assert record.id
    assert record.cv_type == "base"
    assert record.user_id == "test-user-id"
    assert record.cv_json["cv"]["profile_statement"]
    assert record.analysis is None
    assert record.job_description_text is None
    assert record.pdf_path and record.pdf_path.endswith(".pdf")


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.personalize_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.get_user_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_personalize_cv_persists_analysis(db_session):
    record = await cv_generator.personalize_cv(
        db_session, "test-user-id", "Senior Python Engineer at Acme..." * 3
    )

    assert record.cv_type == "personalized"
    assert record.analysis["match_score"] == 78
    assert "Kubernetes" in record.analysis["missing_keywords"]
    assert record.job_description_text.startswith("Senior Python")


async def test_generate_base_cv_without_provider_raises(db_session):
    with pytest.raises(ProviderAuthError):
        # test user has no active_provider -> provider_config is None
        await cv_generator.generate_base_cv(db_session, "test-user-id")


async def test_generate_without_profile_raises(db_session):
    with pytest.raises(ProfileIncompleteError):
        await cv_generator.generate_base_cv(db_session, "no-profile-user")


@patch("app.services.cv_generator.compile_cv", new=MagicMock())
@patch(
    "app.services.cv_generator.generate_base_cv_llm",
    new=AsyncMock(return_value=SAMPLE_OUTPUT),
)
@patch(
    "app.services.cv_generator.personalize_cv_llm",
    new=AsyncMock(return_value=(SAMPLE_ANALYSIS, SAMPLE_OUTPUT)),
)
@patch(
    "app.services.cv_generator.get_user_active_provider_config",
    new=AsyncMock(return_value=PROVIDER_CFG),
)
async def test_list_get_soft_delete_and_rate_count(db_session):
    first = await cv_generator.generate_base_cv(db_session, "test-user-id")
    second = await cv_generator.personalize_cv(
        db_session, "test-user-id", "Backend Engineer role requiring Go and SQL..." * 2
    )

    listed = await cv_generator.list_cvs(db_session, "test-user-id")
    assert {c.id for c in listed} == {first.id, second.id}

    fetched = await cv_generator.get_cv(db_session, "test-user-id", first.id)
    assert fetched.id == first.id

    recent = await cv_generator.count_recent_cvs(db_session, "test-user-id", window_minutes=60)
    assert recent == 2

    await cv_generator.soft_delete_cv(db_session, "test-user-id", first.id)
    assert [c.id for c in await cv_generator.list_cvs(db_session, "test-user-id")] == [second.id]

    with pytest.raises(NotFoundError):
        await cv_generator.get_cv(db_session, "test-user-id", first.id)

    with pytest.raises(NotFoundError):
        await cv_generator.get_cv(db_session, "other-user", second.id)
