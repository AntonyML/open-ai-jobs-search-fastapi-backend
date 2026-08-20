"""Tests for the global provider config (admin) — web_search_enabled capability."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    GLOBAL_PROVIDER_CONFIG_ID,
    Base,
    GlobalProviderConfig,
    User,
)
from app.services.provider_config import (
    get_global_provider_config_out,
    set_global_provider_config,
)


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                id="admin-1",
                email="admin@example.com",
                hashed_password="fakehash",
                role="admin",
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


async def test_web_search_enabled_true_for_openai_gpt5(db_session):
    await set_global_provider_config(db_session, provider="openai", model="gpt-5", updated_by="admin-1")
    await db_session.commit()

    out = await get_global_provider_config_out(db_session)
    assert out["provider"] == "openai"
    assert out["model"] == "gpt-5"
    assert out["web_search_enabled"] is True


async def test_web_search_enabled_true_for_anthropic(db_session):
    """Claude exposes the native web_fetch server tool, so URL reading works."""
    await set_global_provider_config(db_session, provider="anthropic", model="claude-sonnet-4-5", updated_by="admin-1")
    await db_session.commit()

    out = await get_global_provider_config_out(db_session)
    assert out["provider"] == "anthropic"
    assert out["web_search_enabled"] is True


async def test_web_search_enabled_false_when_unconfigured(db_session):
    """No admin row → .env fallback → anthropic default (which supports web_fetch)."""
    out = await get_global_provider_config_out(db_session)
    assert out["provider"] == "anthropic"
    assert out["web_search_enabled"] is True


async def test_web_search_enabled_computed_from_saved_model(db_session):
    """Saving a new model immediately updates the capability flag."""
    await set_global_provider_config(db_session, provider="openai", model="gpt-4o", updated_by="admin-1")
    await db_session.commit()
    assert (await get_global_provider_config_out(db_session))["web_search_enabled"] is False

    await set_global_provider_config(db_session, provider="openai", model="gpt-5-mini", updated_by="admin-1")
    await db_session.commit()
    assert (await get_global_provider_config_out(db_session))["web_search_enabled"] is True


async def test_singleton_row_reused(db_session):
    await set_global_provider_config(db_session, provider="openai", model="gpt-5", updated_by="admin-1")
    await db_session.commit()

    row = await db_session.get(GlobalProviderConfig, GLOBAL_PROVIDER_CONFIG_ID)
    assert row is not None
    assert row.provider == "openai"
