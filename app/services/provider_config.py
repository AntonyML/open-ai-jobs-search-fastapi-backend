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
from app.llm.adapter import has_web_search_support

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
        model = row.model or (await _default_model_for(row.provider))
        config = {
            "provider": row.provider,
            "display_name": PROVIDER_DISPLAY_MAP.get(row.provider),
            "model": model,
            "api_base": row.api_base,
            "has_key": bool(row.api_key_encrypted),
            "last_status": row.last_status,
            "last_error": row.last_error,
            "last_checked_at": row.last_checked_at,
            "updated_by": row.updated_by,
            "updated_at": row.updated_at,
            "web_search_enabled": _web_search_for(row.provider, model),
        }
        # Notify admin when no API key is stored (debounced: only if no
        # recent unread notification of this type exists).
        if not config["has_key"]:
            await _notify_admin_no_provider(db)
        return config

    from app.core.settings import get_settings

    settings = get_settings()
    display = PROVIDER_DISPLAY_MAP.get(settings.llm_default_provider)
    model = await _default_model_for(settings.llm_default_provider)
    # Always notify when falling back to .env with no stored key.
    await _notify_admin_no_provider(db)
    return {
        "provider": settings.llm_default_provider,
        "display_name": display,
        "model": model,
        "api_base": None,
        "has_key": False,
        "last_status": None,
        "last_error": None,
        "last_checked_at": None,
        "updated_by": None,
        "updated_at": None,
        "web_search_enabled": _web_search_for(settings.llm_default_provider, model),
    }


async def _notify_admin_no_provider(db: AsyncSession) -> None:
    """Send in-app notification + email to the first admin when no provider
    key is configured.  Debounced: skips if an unread ``no_provider_alert``
    notification already exists (avoids spam on every profile page load).
    """
    from sqlalchemy import select

    from app.core.settings import get_settings
    from app.db.models import AppNotification, User
    from app.services.notifications import notify_admin

    # Check for existing unread alert to avoid duplicate notifications.
    admin_result = await db.execute(select(User).where(User.role == "admin").order_by(User.created_at.asc()).limit(1))
    admin = admin_result.scalar_one_or_none()
    if admin is None:
        return

    existing = await db.execute(
        select(AppNotification.id)
        .where(
            AppNotification.user_id == admin.id,
            AppNotification.type == "no_provider_alert",
            AppNotification.is_read == False,  # noqa: E712
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return  # Already has an unread alert — don't spam.

    await notify_admin(
        db,
        type_="no_provider_alert",
        title="Sin proveedor de IA configurado",
        body="El sistema no tiene una clave API de proveedor de IA configurada."
        " Algunas funcionalidades de IA pueden no estar disponibles.",
        payload={"action": "configure_provider", "href": "/admin/providers"},
    )
    await db.flush()

    # Send email asynchronously (fire-and-forget; failures are logged).
    try:
        from app.services.email import send_no_provider_notification

        settings = get_settings()
        await send_no_provider_notification(admin_email=settings.admin_email)
    except Exception:
        pass  # Best-effort; the in-app notification is the primary channel.


def _web_search_for(provider: str | None, model: str | None) -> bool:
    """Whether the given provider/model combination supports the web_search tool."""
    if not provider or not model:
        return False
    return has_web_search_support(f"{provider}/{model}")


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
