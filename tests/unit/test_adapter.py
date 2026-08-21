"""Tests for the LiteLLM adapter — _build_kwargs, provider routing, and key resolution."""

from __future__ import annotations

from app.llm.adapter import _build_kwargs


class TestBuildKwargs:
    """Tests for _build_kwargs model prefix logic."""

    def test_anthropic_uses_anthropic_prefix(self):
        kwargs = _build_kwargs("anthropic", "claude-sonnet-4-6", api_key="sk-ant")
        assert kwargs["model"] == "anthropic/claude-sonnet-4-6"
        assert kwargs["api_key"] == "sk-ant"
        assert "api_base" not in kwargs

    def test_openai_uses_openai_prefix(self):
        kwargs = _build_kwargs("openai", "gpt-4.1", api_key="sk-oa")
        assert kwargs["model"] == "openai/gpt-4.1"
        assert kwargs["api_key"] == "sk-oa"

    def test_gemini_uses_gemini_prefix(self):
        kwargs = _build_kwargs("gemini", "gemini-2.5-flash", api_key="AIza...")
        assert kwargs["model"] == "gemini/gemini-2.5-flash"
        assert kwargs["api_key"] == "AIza..."

    def test_nvidia_nim_uses_nvidia_nim_prefix(self):
        kwargs = _build_kwargs(
            "nvidia_nim", "meta/llama-3.3-70b-instruct",
            api_key="nvapi-xxx", api_base="https://integrate.api.nvidia.com/v1",
        )
        assert kwargs["model"] == "nvidia_nim/meta/llama-3.3-70b-instruct"
        assert kwargs["api_base"] == "https://integrate.api.nvidia.com/v1"

    def test_custom_uses_openai_prefix(self):
        """Custom providers map to openai/ for LiteLLM (OpenAI-compatible gateway)."""
        kwargs = _build_kwargs(
            "custom", "my-llm-7b",
            api_key="sk-custom", api_base="https://api.example.com/v1",
        )
        assert kwargs["model"] == "openai/my-llm-7b"
        assert kwargs["api_key"] == "sk-custom"
        assert kwargs["api_base"] == "https://api.example.com/v1"

    def test_lm_studio_uses_lm_studio_prefix(self):
        kwargs = _build_kwargs(
            "lm_studio", "local-model",
            api_base="http://localhost:1234/v1",
        )
        assert kwargs["model"] == "lm_studio/local-model"
        assert kwargs["api_base"] == "http://localhost:1234/v1"
        assert "api_key" not in kwargs

    def test_ollama_uses_ollama_prefix(self):
        kwargs = _build_kwargs("ollama", "llama3.2", api_base="http://localhost:11434")
        assert kwargs["model"] == "ollama/llama3.2"

    def test_no_optional_keys_when_none(self):
        """When api_key and api_base are None, they are omitted from kwargs."""
        kwargs = _build_kwargs("anthropic", "claude-sonnet-4-6")
        assert "api_key" not in kwargs
        assert "api_base" not in kwargs


class TestProviderCatalog:
    """Verify the provider catalog metadata is consistent."""

    def test_all_providers_have_required_fields(self):
        from app.schemas.providers import KNOWN_PROVIDERS

        for p in KNOWN_PROVIDERS:
            assert p.name, f"Provider {p} has no name"
            assert p.display_name, f"Provider {p.name} has no display_name"
            assert p.default_model is not None, f"Provider {p.name} has no default_model"

    def test_static_listing_providers_have_models(self):
        """Providers with static_listing=True must have a non-empty static_models list."""
        from app.schemas.providers import KNOWN_PROVIDERS

        for p in KNOWN_PROVIDERS:
            if p.static_listing:
                assert p.static_models is not None and len(p.static_models) > 0, (
                    f"Provider '{p.name}' has static_listing=True but no static_models"
                )

    def test_custom_provider_metadata(self):
        from app.schemas.providers import KNOWN_PROVIDERS

        custom = next(p for p in KNOWN_PROVIDERS if p.name == "custom")
        assert custom.supports_custom_base is True
        assert custom.requires_api_key is True
        assert custom.static_listing is False
        assert custom.default_model == ""

    def test_gemini_provider_metadata(self):
        from app.schemas.providers import KNOWN_PROVIDERS

        gemini = next(p for p in KNOWN_PROVIDERS if p.name == "gemini")
        assert gemini.static_listing is True
        assert gemini.requires_api_key is True
        assert gemini.supports_custom_base is False
        assert "gemini-2.5-flash" in gemini.static_models
        assert "gemini-2.5-pro" in gemini.static_models
