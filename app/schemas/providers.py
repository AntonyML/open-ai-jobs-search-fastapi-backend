"""Pydantic schemas for LLM provider credentials management."""

from typing import Any

from pydantic import BaseModel, Field, HttpUrl


# ── Request schemas ─────────────────────────────────────────────────


class ProviderCredentialCreate(BaseModel):
    """Create or update a provider credential for the current user."""

    provider: str = Field(
        ...,
        description="Provider name: anthropic, openai, nvidia_nim, lm_studio, etc.",
        examples=["anthropic", "openai", "nvidia_nim", "lm_studio"],
    )
    api_key: str = Field(..., description="Plaintext API key (will be encrypted at rest)")
    api_base: HttpUrl | None = Field(
        None,
        description="Base URL for self-hosted providers (e.g., LM Studio, NVIDIA NIM)",
        examples=["http://localhost:1234/v1", "https://integrate.api.nvidia.com/v1"],
    )
    model: str | None = Field(
        None,
        description="Model name to use with this provider",
        examples=["claude-sonnet-4-20250514", "gpt-4o", "meta/llama-3.1-70b-instruct"],
    )


class ProviderCredentialUpdate(BaseModel):
    """Update an existing provider credential (partial)."""

    api_key: str | None = Field(None, description="New API key")
    api_base: HttpUrl | None = Field(None, description="New base URL")
    model: str | None = Field(None, description="New model name")


class SetActiveProvider(BaseModel):
    """Set the user's active LLM provider."""

    provider: str = Field(..., description="Provider name to set as active")


# ── Response schemas ────────────────────────────────────────────────


class ProviderCredentialOut(BaseModel):
    """Provider credential response (without API key)."""

    provider: str
    api_base: str | None
    model: str | None
    has_key: bool = True
    is_active: bool

    model_config = {"from_attributes": True}


class ProviderInfo(BaseModel):
    """Available provider metadata for UI."""

    name: str
    display_name: str
    requires_api_key: bool
    supports_custom_base: bool
    default_model: str
    example_base_url: str | None = None


class ActiveProviderOut(BaseModel):
    """Current active provider configuration for the user."""

    provider: str
    model: str
    api_base: str | None
    has_credential: bool


# ── Known providers catalog ─────────────────────────────────────────

KNOWN_PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(
        name="anthropic",
        display_name="Anthropic (Claude)",
        requires_api_key=True,
        supports_custom_base=False,
        default_model="claude-sonnet-4-20250514",
    ),
    ProviderInfo(
        name="openai",
        display_name="OpenAI (GPT)",
        requires_api_key=True,
        supports_custom_base=False,
        default_model="gpt-4o",
    ),
    ProviderInfo(
        name="nvidia_nim",
        display_name="NVIDIA NIM",
        requires_api_key=True,
        supports_custom_base=True,
        default_model="meta/llama-3.1-70b-instruct",
        example_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ProviderInfo(
        name="lm_studio",
        display_name="LM Studio (Local)",
        requires_api_key=False,
        supports_custom_base=True,
        default_model="local-model",
        example_base_url="http://localhost:1234/v1",
    ),
    ProviderInfo(
        name="ollama",
        display_name="Ollama (Local)",
        requires_api_key=False,
        supports_custom_base=True,
        default_model="llama3.1",
        example_base_url="http://localhost:11434/v1",
    ),
]