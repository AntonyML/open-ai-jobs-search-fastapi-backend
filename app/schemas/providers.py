"""Pydantic schemas for LLM provider credentials management."""

from typing import Any, Literal

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
    usage_limits: dict | None = None

    model_config = {"from_attributes": True}


class ProviderInfo(BaseModel):
    """Available provider metadata for UI."""

    name: str
    display_name: str
    requires_api_key: bool
    supports_custom_base: bool
    default_model: str
    example_base_url: str | None = None
    static_models: list[str] | None = Field(
        None,
        description="Curated static model list for providers without a public list-models endpoint (e.g. Anthropic).",
    )


class ActiveProviderOut(BaseModel):
    """Current active provider configuration for the user."""

    provider: str | None = None
    model: str | None = None
    api_base: str | None = None
    has_credential: bool = False


# ── Model listing & selection schemas ───────────────────────────────


class ModelInfo(BaseModel):
    """A single model offered by a provider."""

    id: str = Field(..., description="Model identifier as returned by the provider")
    object: str | None = Field(None, description="Object type (e.g. 'model' for OpenAI-compatible)")
    owned_by: str | None = Field(None, description="Owner of the model, if reported")
    source: Literal["live", "static"] = Field(
        ..., description="Whether the model came from a live API call or a static curated list"
    )


class ModelListOut(BaseModel):
    """List of models available for a provider."""

    provider: str
    source: Literal["live", "static"]
    models: list[ModelInfo]


class SetModelSelection(BaseModel):
    """Persist the user's chosen model for a provider."""

    model: str = Field(..., description="Model identifier to select for this provider")


class ActiveModelOut(BaseModel):
    """The model the user has selected for a given provider."""

    provider: str
    model: str | None = Field(None, description="Selected model, or null if none chosen yet")


# ── Known providers catalog ─────────────────────────────────────────

KNOWN_PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(
        name="anthropic",
        display_name="Anthropic (Claude)",
        requires_api_key=True,
        supports_custom_base=False,
        default_model="claude-sonnet-4-6",  # was: claude-sonnet-4-20250514 (deprecated snapshot format)
        static_models=[
            "claude-sonnet-4-5",
            "claude-opus-4-1",
            "claude-haiku-4-5",
            "claude-sonnet-4-20250514",
            "claude-3-5-haiku-20241022",
        ],
    ),
    ProviderInfo(
        name="openai",
        display_name="OpenAI (GPT)",
        requires_api_key=True,
        supports_custom_base=False,
        default_model="gpt-4.1",  # was: gpt-4o (retired from ChatGPT, gpt-4.1 es el sucesor API-first)
    ),
    ProviderInfo(
        name="nvidia_nim",
        display_name="NVIDIA NIM",
        requires_api_key=True,
        supports_custom_base=True,
        default_model="meta/llama-3.3-70b-instruct",  # was: llama-3.1-70b-instruct (3.3 es el actual en NIM)
        example_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ProviderInfo(
        name="lm_studio",
        display_name="LM Studio (Local)",
        requires_api_key=False,
        supports_custom_base=True,
        default_model="local-model",  # sin cambio, depende del modelo cargado
        example_base_url="http://localhost:1234/v1",
    ),
    ProviderInfo(
        name="ollama",
        display_name="Ollama (Local)",
        requires_api_key=False,
        supports_custom_base=True,
        default_model="llama3.2",  # was: llama3.1 (3.2 es el tag actual en ollama hub)
        example_base_url="http://localhost:11434/v1",
    ),
]