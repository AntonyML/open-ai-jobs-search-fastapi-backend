"""Tests for provider_models service + admin providers router endpoints."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import encrypt_api_key
from app.db.models import (
    GLOBAL_PROVIDER_CONFIG_ID,
    Base,
    GlobalProviderConfig,
    User,
    UserModelSelection,
)
from app.exceptions import LLMError, NotFoundError, ProviderAuthError
from app.schemas.providers import ModelInfo, ModelListOut
from app.services.provider_models import (
    _static_models_for,
    list_provider_models,
    set_user_model_selection,
)
from app.services.provider_models import (
    get_user_model_selection as get_user_model_selection_svc,
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

    def test_openai_returns_static_fallback_list(self):
        """OpenAI has a static fallback list (includes web-search models)."""
        result = _static_models_for("openai")
        assert isinstance(result, list)
        assert "gpt-5" in result
        assert "gpt-4o-search-preview" in result

    def test_gemini_returns_static_list(self):
        result = _static_models_for("gemini")
        assert isinstance(result, list)
        assert len(result) > 0
        assert "gemini-2.5-flash" in result
        assert "gemini-2.5-pro" in result

    def test_custom_returns_none(self):
        """Custom provider has no static model list."""
        assert _static_models_for("custom") is None

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
        from unittest.mock import AsyncMock, patch

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
        from unittest.mock import AsyncMock, patch

        with (
            patch(
                "app.services.provider_models._list_openai_compatible",
                new_callable=AsyncMock,
                side_effect=LLMError("HTTP 401"),
            ),
            pytest.raises(LLMError, match="HTTP 401"),
        ):
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
        from unittest.mock import AsyncMock, patch

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
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.services.provider_models._list_ollama",
            new_callable=AsyncMock,
            return_value=mock_models,
        ):
            result = await list_provider_models(db_session, "ollama")
        assert result.provider == "ollama"
        assert result.source == "live"

    # ── Gemini ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_gemini_returns_static_list(self, db_session):
        """Gemini uses a curated static model list (no live HTTP call)."""
        result = await list_provider_models(db_session, "gemini")
        assert result.provider == "gemini"
        assert result.source == "static"
        assert len(result.models) > 0
        model_ids = [m.id for m in result.models]
        assert "gemini-2.5-flash" in model_ids
        assert "gemini-2.5-pro" in model_ids

    @pytest.mark.asyncio
    async def test_gemini_static_listing_flag(self):
        """Gemini has static_listing=True in the catalog."""
        from app.schemas.providers import KNOWN_PROVIDERS

        gemini = next(p for p in KNOWN_PROVIDERS if p.name == "gemini")
        assert gemini.static_listing is True
        assert gemini.requires_api_key is True
        assert gemini.supports_custom_base is False

    # ── Custom (OpenAI-compatible) ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_custom_no_api_base_raises(self, db_session):
        """Custom provider requires api_base to list models."""
        db_session.add(_make_global_config("custom", api_base=None))
        await db_session.commit()
        with pytest.raises(ProviderAuthError, match="requires an api_base"):
            await list_provider_models(db_session, "custom")

    @pytest.mark.asyncio
    async def test_custom_no_key_raises(self, db_session):
        """Custom provider requires an API key."""
        db_session.add(_make_global_config("custom", api_key=None, api_base="https://api.example.com/v1"))
        await db_session.commit()
        with pytest.raises(ProviderAuthError, match="No API key configured"):
            await list_provider_models(db_session, "custom")

    @pytest.mark.asyncio
    async def test_custom_live_success(self, db_session):
        """Custom provider lists models via OpenAI-compatible endpoint."""
        db_session.add(_make_global_config("custom", api_base="https://api.example.com/v1"))
        await db_session.commit()
        mock_models = [ModelInfo(id="my-model-7b", object="model", owned_by="custom", source="live")]
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.services.provider_models._list_openai_compatible",
            new_callable=AsyncMock,
            return_value=mock_models,
        ):
            result = await list_provider_models(db_session, "custom")
        assert result.provider == "custom"
        assert result.source == "live"
        assert len(result.models) == 1
        assert result.models[0].id == "my-model-7b"

    @pytest.mark.asyncio
    async def test_custom_catalog_entry(self):
        """Custom provider catalog entry has correct metadata."""
        from app.schemas.providers import KNOWN_PROVIDERS

        custom = next(p for p in KNOWN_PROVIDERS if p.name == "custom")
        assert custom.static_listing is False
        assert custom.requires_api_key is True
        assert custom.supports_custom_base is True
        assert custom.example_base_url == "https://api.example.com/v1"

    # ── Unknown ─────────────────────────────────────────────────────

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

    def test_catalog_includes_all_providers(self):
        """KNOWN_PROVIDERS catalog includes all expected providers."""
        from app.schemas.providers import KNOWN_PROVIDERS, PROVIDER_DISPLAY_MAP

        names = [p.name for p in KNOWN_PROVIDERS]
        assert "anthropic" in names
        assert "openai" in names
        assert "nvidia_nim" in names
        assert "lm_studio" in names
        assert "ollama" in names
        assert "gemini" in names
        assert "custom" in names
        assert len(KNOWN_PROVIDERS) == 7

        # PROVIDER_DISPLAY_MAP mirrors catalog
        assert PROVIDER_DISPLAY_MAP["gemini"] == "Google Gemini"
        assert PROVIDER_DISPLAY_MAP["custom"] == "Custom (OpenAI-Compatible)"
