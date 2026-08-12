"""Tests for provider_models service + admin providers router endpoints."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import encrypt_api_key
from app.db.models import (
    Base,
    GLOBAL_PROVIDER_CONFIG_ID,
    GlobalProviderConfig,
    User,
    UserModelSelection,
)
from app.exceptions import LLMError, NotFoundError, ProviderAuthError
from app.schemas.providers import ModelInfo, ModelListOut
from app.services.provider_models import (
    _static_models_for,
    get_user_model_selection as get_user_model_selection_svc,
    list_provider_models,
    set_user_model_selection,
)


@pytest.fixture
async def db_session():
    """In-memory SQLite database with a test user."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            id="test-user-id",
            email="test@example.com",
            hashed_password="fakehash",
            full_name="Test User",
            active_provider="anthropic",
        )
        session.add(user)
        await session.commit()
        yield session
    await engine.dispose()


def _make_global_config(provider, api_key="sk-test", api_base=None):
    return GlobalProviderConfig(
        id=GLOBAL_PROVIDER_CONFIG_ID,
        provider=provider,
        api_key_encrypted=encrypt_api_key(api_key) if api_key else None,
        api_base=api_base,
    )


class TestStaticModelsFor:
    def test_anthropic_returns_static_list(self):
        result = _static_models_for("anthropic")
        assert isinstance(result, list)
        assert len(result) > 0
        assert "claude-sonnet-4-5" in result

    def test_openai_returns_none(self):
        assert _static_models_for("openai") is None

    def test_unknown_returns_none(self):
        assert _static_models_for("unknown_provider") is None


class TestListProviderModels:
    @pytest.mark.asyncio
    async def test_anthropic_returns_static_list(self, db_session):
        result = await list_provider_models(db_session, "anthropic")
        assert result.provider == "anthropic"
        assert result.source == "static"
        assert len(result.models) > 0

    @pytest.mark.asyncio
    async def test_openai_no_key_raises(self, db_session):
        with pytest.raises(ProviderAuthError, match="No API key configured"):
            await list_provider_models(db_session, "openai")

    @pytest.mark.asyncio
    async def test_openai_live_success(self, db_session):
        db_session.add(_make_global_config("openai"))
        await db_session.commit()
        mock_models = [ModelInfo(id="gpt-4o", object="model", owned_by="openai", source="live")]
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.services.provider_models._list_openai_compatible",
            new_callable=AsyncMock,
            return_value=mock_models,
        ):
            result = await list_provider_models(db_session, "openai")
        assert result.provider == "openai"
        assert result.source == "live"
        assert len(result.models) == 1

    @pytest.mark.asyncio
    async def test_openai_http_error(self, db_session):
        db_session.add(_make_global_config("openai"))
        await db_session.commit()
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.services.provider_models._list_openai_compatible",
            new_callable=AsyncMock,
            side_effect=LLMError("HTTP 401"),
        ):
            with pytest.raises(LLMError, match="HTTP 401"):
                await list_provider_models(db_session, "openai")

    @pytest.mark.asyncio
    async def test_lm_studio_no_api_base_raises(self, db_session):
        db_session.add(_make_global_config("lm_studio", api_base=None))
        await db_session.commit()
        with pytest.raises(ProviderAuthError, match="requires an api_base"):
            await list_provider_models(db_session, "lm_studio")

    @pytest.mark.asyncio
    async def test_lm_studio_live_success(self, db_session):
        db_session.add(_make_global_config("lm_studio", api_base="http://localhost:1234/v1"))
        await db_session.commit()
        mock_models = [ModelInfo(id="local-model", object="model", owned_by="lmstudio", source="live")]
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.services.provider_models._list_openai_compatible",
            new_callable=AsyncMock,
            return_value=mock_models,
        ):
            result = await list_provider_models(db_session, "lm_studio")
        assert result.provider == "lm_studio"
        assert result.source == "live"

    @pytest.mark.asyncio
    async def test_ollama_no_api_base_raises(self, db_session):
        db_session.add(_make_global_config("ollama", api_base=None))
        await db_session.commit()
        with pytest.raises(ProviderAuthError, match="requires an api_base"):
            await list_provider_models(db_session, "ollama")

    @pytest.mark.asyncio
    async def test_ollama_live_success(self, db_session):
        db_session.add(_make_global_config("ollama", api_base="http://localhost:11434"))
        await db_session.commit()
        mock_models = [ModelInfo(id="llama3.2:3b", object="model", owned_by="llama", source="live")]
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.services.provider_models._list_ollama",
            new_callable=AsyncMock,
            return_value=mock_models,
        ):
            result = await list_provider_models(db_session, "ollama")
        assert result.provider == "ollama"
        assert result.source == "live"

    @pytest.mark.asyncio
    async def test_unknown_provider_raises(self, db_session):
        with pytest.raises(NotFoundError, match="Unknown provider"):
            await list_provider_models(db_session, "unknown_provider")


class TestSetUserModelSelection:
    @pytest.mark.asyncio
    async def test_insert_new_selection(self, db_session):
        await set_user_model_selection(db_session, "test-user-id", "openai", "gpt-4.1")
        await db_session.commit()
        result = await get_user_model_selection_svc(db_session, "test-user-id", "openai")
        assert result == "gpt-4.1"

    @pytest.mark.asyncio
    async def test_upsert_existing_selection(self, db_session):
        await set_user_model_selection(db_session, "test-user-id", "openai", "gpt-4o")
        await db_session.commit()
        await set_user_model_selection(db_session, "test-user-id", "openai", "gpt-4.1")
        await db_session.commit()
        result = await get_user_model_selection_svc(db_session, "test-user-id", "openai")
        assert result == "gpt-4.1"
        from sqlalchemy import select as sa_select

        rows = await db_session.execute(
            sa_select(UserModelSelection).where(
                UserModelSelection.user_id == "test-user-id",
                UserModelSelection.provider == "openai",
            )
        )
        assert len(rows.scalars().all()) == 1

    @pytest.mark.asyncio
    async def test_get_unset_returns_none(self, db_session):
        result = await get_user_model_selection_svc(db_session, "test-user-id", "openai")
        assert result is None


class TestAdminProvidersRouterSmoke:
    def test_routes_registered(self):
        from app.api.v1.admin import router

        routes = [r.path for r in router.routes]
        assert "/admin/providers" in routes
        assert "/admin/providers/{provider}/models" in routes
        assert "/admin/providers/test" in routes

    def test_anthropic_static_response_shape(self):
        """Verify the static Anthropic response matches ModelListOut schema."""
        data = ModelListOut(
            provider="anthropic",
            source="static",
            models=[ModelInfo(id="claude-sonnet-4-5", source="static")],
        )
        assert data.provider == "anthropic"
        assert data.source == "static"
        assert len(data.models) == 1

    @pytest.mark.asyncio
    async def test_service_to_schema_roundtrip(self, db_session: AsyncSession):
        """Verify list_provider_models returns a valid ModelListOut."""
        result = await list_provider_models(db_session, "anthropic")
        validated = ModelListOut(provider=result.provider, source=result.source, models=result.models)
        assert validated.provider == "anthropic"
        assert validated.source == "static"
