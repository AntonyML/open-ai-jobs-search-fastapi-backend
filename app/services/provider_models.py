"""Provider models service — list available models and persist user selection.

For providers with a public list-models endpoint (OpenAI, NVIDIA NIM, LM Studio,
Ollama) the list is fetched live via httpx.  For Anthropic (no public list
endpoint) a curated static list is returned from the KNOWN_PROVIDERS catalog.

Authentication for live calls uses the user's stored (decrypted) credential.
There is no fallback to .env — if the user has not stored a credential for the
requested provider, ProviderAuthError is raised.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserModelSelection
from app.exceptions import LLMError, NotFoundError, ProviderAuthError
from app.schemas.providers import ModelInfo, ModelListOut
from app.services.provider_credentials import get_provider_credential

# Default api_base per provider (used only when the credential has no api_base)
_DEFAULT_API_BASE: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "nvidia_nim": "https://integrate.api.nvidia.com/v1",
    "lm_studio": "http://localhost:1234/v1",
    "ollama": "http://localhost:11434",
}

# Providers that do not require an API key (local servers)
_NO_KEY_PROVIDERS = {"lm_studio", "ollama"}

# Timeout for live model-listing calls (seconds)
_LIST_TIMEOUT = 10.0


def _static_models_for(provider: str) -> list[str] | None:
    """Return the curated static model list for a provider, if any."""
    from app.schemas.providers import KNOWN_PROVIDERS

    for info in KNOWN_PROVIDERS:
        if info.name == provider:
            return info.static_models
    return None


async def _resolve_credential(
    db: AsyncSession,
    user_id: str,
    provider: str,
) -> tuple[str | None, str | None]:
    """Return (decrypted_api_key, api_base) for the user's stored credential.

    Raises ProviderAuthError if no credential is stored, or (for local
    providers) if api_base is missing.
    """
    credential = await get_provider_credential(db, user_id, provider)
    if credential is None:
        raise ProviderAuthError(
            f"No credential stored for provider '{provider}'. "
            f"Add it via POST /providers before listing models."
        )

    api_key = credential.api_key_encrypted  # already decrypted by get_provider_credential
    api_base = credential.api_base

    # Local providers need an api_base to know where to call
    if provider in _NO_KEY_PROVIDERS and not api_base:
        raise ProviderAuthError(
            f"Provider '{provider}' requires an api_base to be stored. "
            f"Update it via PATCH /providers/{provider} with an api_base before listing models."
        )

    return api_key, api_base


async def _list_openai_compatible(
    url: str,
    api_key: str | None,
    provider: str,
) -> list[ModelInfo]:
    """GET {url}/models — OpenAI-compatible response shape."""
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=_LIST_TIMEOUT) as client:
            resp = await client.get(f"{url.rstrip('/')}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(
            f"{provider} returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc
    except httpx.RequestError as exc:
        raise LLMError(f"Could not reach {provider} at {url}: {exc}") from exc

    items = data.get("data", []) if isinstance(data, dict) else []
    models: list[ModelInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not model_id:
            continue
        models.append(
            ModelInfo(
                id=model_id,
                object=item.get("object"),
                owned_by=item.get("owned_by"),
                source="live",
            )
        )
    return models


async def _list_ollama(url: str) -> list[ModelInfo]:
    """GET {url}/api/tags — Ollama response shape."""
    try:
        async with httpx.AsyncClient(timeout=_LIST_TIMEOUT) as client:
            resp = await client.get(f"{url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(
            f"ollama returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc
    except httpx.RequestError as exc:
        raise LLMError(f"Could not reach ollama at {url}: {exc}") from exc

    items = data.get("models", []) if isinstance(data, dict) else []
    models: list[ModelInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        models.append(
            ModelInfo(
                id=name,
                object=None,
                owned_by=item.get("details", {}).get("family") if isinstance(item.get("details"), dict) else None,
                source="live",
            )
        )
    return models


async def list_provider_models(
    db: AsyncSession,
    user_id: str,
    provider: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ModelListOut:
    """List the models available to the user for the given provider.

    For Anthropic, returns a curated static list (no HTTP call).
    For OpenAI/NVIDIA NIM/LM Studio, uses the OpenAI-compatible /models endpoint.
    For Ollama, uses /api/tags.

    Raises:
        ProviderAuthError: no credential stored (or no api_base for local providers).
        LLMError: the live call failed or could not be parsed.
    """
    # Anthropic: static curated list, no credential needed for listing
    if provider == "anthropic":
        static = _static_models_for("anthropic") or []
        return ModelListOut(
            provider=provider,
            source="static",
            models=[ModelInfo(id=m, object=None, owned_by="anthropic", source="static") for m in static],
        )

    # Unknown provider?
    if provider not in _DEFAULT_API_BASE:
        raise NotFoundError(f"Unknown provider '{provider}'")

    if api_key is None and api_base is None:
        api_key, api_base = await _resolve_credential(db, user_id, provider)
    base_url = api_base or _DEFAULT_API_BASE[provider]

    if provider == "ollama":
        models = await _list_ollama(base_url)
    else:  # openai, nvidia_nim, lm_studio are OpenAI-compatible
        models = await _list_openai_compatible(base_url, api_key, provider)

    return ModelListOut(provider=provider, source="live", models=models)


async def set_user_model_selection(
    db: AsyncSession,
    user_id: str,
    provider: str,
    model: str,
) -> UserModelSelection:
    """Upsert the user's selected model for a provider.

    One row per (user_id, provider) — updates the model if a row exists.
    """
    result = await db.execute(
        select(UserModelSelection).where(
            UserModelSelection.user_id == user_id,
            UserModelSelection.provider == provider,
        )
    )
    selection = result.scalar_one_or_none()

    if selection is None:
        selection = UserModelSelection(
            user_id=user_id,
            provider=provider,
            model=model,
        )
        db.add(selection)
    else:
        selection.model = model

    await db.flush()
    await db.refresh(selection)
    return selection


async def get_user_model_selection(
    db: AsyncSession,
    user_id: str,
    provider: str,
) -> str | None:
    """Return the user's selected model for a provider, or None if unset."""
    result = await db.execute(
        select(UserModelSelection).where(
            UserModelSelection.user_id == user_id,
            UserModelSelection.provider == provider,
        )
    )
    selection = result.scalar_one_or_none()
    return selection.model if selection is not None else None
