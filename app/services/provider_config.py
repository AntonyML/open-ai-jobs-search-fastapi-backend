"""Global provider config service — admin-managed singleton LLM provider.

Replaces the old per-user ``provider_credentials`` flow.  There is exactly
one ``GlobalProviderConfig`` row (fixed primary key, see
``GLOBAL_PROVIDER_CONFIG_ID``).  When the row is empty (provider NULL) the
system falls back to ``.env`` settings until an admin configures the
global provider.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_api_key, encrypt_api_key
from app.db.models import GLOBAL_PROVIDER_CONFIG_ID, GlobalProviderConfig

# Sentinel sent by the admin frontend when editing without changing the key.
MASKED_KEY = "__MASKED__"


async def get_global_provider_config(db: AsyncSession) -> GlobalProviderConfig | None:
    """Return the singleton global provider config row, or None."""
    return await db.get(GlobalProviderConfig, GLOBAL_PROVIDER_CONFIG_ID)


async def _default_model_for(provider: str) -> str | None:
    from app.schemas.providers import KNOWN_PROVIDERS

    for info in KNOWN_PROVIDERS:
        if info.name == provider:
            return info.default_model
    return None


async def get_active_provider_config(db: AsyncSession) -> dict[str, Any]:
    """Resolve the effective LLM provider configuration.

    Priority:
    1. Global admin config stored in ``global_provider_config`` (if provider set).
    2. ``.env`` settings fallback (``llm_default_provider`` + env API keys).

    Returns a dict with keys: provider, model, api_key, api_base.
    """
    from app.core.settings import get_settings

    settings = get_settings()
    row = await get_global_provider_config(db)

    if row is not None and row.provider:
        model = row.model or (await _default_model_for(row.provider))
        api_key = None
        if row.api_key_encrypted:
            try:
                api_key = decrypt_api_key(row.api_key_encrypted)
            except Exception:
                api_key = row.api_key_encrypted
        return {
            "provider": row.provider,
            "model": model,
            "api_key": api_key,
            "api_base": row.api_base,
        }

    return {
        "provider": settings.llm_default_provider,
        "model": None,
        "api_key": None,
        "api_base": None,
    }


async def get_global_provider_config_out(db: AsyncSession) -> dict[str, Any]:
    """Build the admin-facing serialisable config (no plaintext key).

    When the row exists and ``provider`` is set this reflects the admin
    config; otherwise the ``.env`` fallback.  ``has_key`` indicates whether
    an encrypted API key is stored globally.
    """
    from app.schemas.providers import PROVIDER_DISPLAY_MAP

    row = await get_global_provider_config(db)

    if row is not None and row.provider:
        return {
            "provider": row.provider,
            "display_name": PROVIDER_DISPLAY_MAP.get(row.provider),
            "model": row.model or (await _default_model_for(row.provider)),
            "api_base": row.api_base,
            "has_key": bool(row.api_key_encrypted),
            "last_status": row.last_status,
            "last_error": row.last_error,
            "last_checked_at": row.last_checked_at,
            "updated_by": row.updated_by,
            "updated_at": row.updated_at,
        }

    from app.core.settings import get_settings

    settings = get_settings()
    display = PROVIDER_DISPLAY_MAP.get(settings.llm_default_provider)
    return {
        "provider": settings.llm_default_provider,
        "display_name": display,
        "model": await _default_model_for(settings.llm_default_provider),
        "api_base": None,
        "has_key": False,
        "last_status": None,
        "last_error": None,
        "last_checked_at": None,
        "updated_by": None,
        "updated_at": None,
    }


async def set_global_provider_config(
    db: AsyncSession,
    *,
    provider: str,
    api_key: str | None = None,
    api_base: str | None = None,
    model: str | None = None,
    updated_by: str | None = None,
) -> GlobalProviderConfig:
    """Upsert the singleton global provider config row.

    ``api_key`` of ``__MASKED__`` keeps the stored encrypted key unchanged.
    Passing ``api_key=None`` also keeps the stored key; pass an empty string
    to explicitly clear it.
    """
    row = await db.get(GlobalProviderConfig, GLOBAL_PROVIDER_CONFIG_ID)
    if row is None:
        row = GlobalProviderConfig(id=GLOBAL_PROVIDER_CONFIG_ID)
        db.add(row)

    row.provider = provider
    if api_base is not None:
        row.api_base = api_base or None
    if model is not None:
        row.model = model or None

    if api_key is not None and api_key != MASKED_KEY:
        if api_key == "":
            row.api_key_encrypted = None
        else:
            row.api_key_encrypted = encrypt_api_key(api_key)

    row.updated_by = updated_by
    # Reset transient health state — the config changed, so any cached status
    # is stale until the next test call.
    row.last_status = None
    row.last_error = None
    row.last_checked_at = None

    await db.flush()
    await db.refresh(row)
    return row


async def clear_global_provider_config(db: AsyncSession) -> None:
    """Empty the singleton config so the system falls back to ``.env``."""
    row = await db.get(GlobalProviderConfig, GLOBAL_PROVIDER_CONFIG_ID)
    if row is None:
        return
    row.provider = None
    row.model = None
    row.api_key_encrypted = None
    row.api_base = None
    row.last_status = None
    row.last_error = None
    row.last_checked_at = None
    await db.flush()