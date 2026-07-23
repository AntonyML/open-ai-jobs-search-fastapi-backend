"""Providers router — endpoints for managing user LLM provider credentials."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.providers import (
    ActiveModelOut,
    ActiveProviderOut,
    ModelListOut,
    ProviderCredentialCreate,
    ProviderCredentialOut,
    ProviderCredentialUpdate,
    ProviderInfo,
    SetActiveProvider,
    SetModelSelection,
)
from app.services.provider_credentials import (
    delete_provider_credential,
    get_provider_credential,
    get_user_active_provider_config,
    list_user_providers,
    set_provider_credential,
    set_user_active_provider,
)
from app.services.provider_models import (
    get_user_model_selection,
    list_provider_models,
    set_user_model_selection,
)
from app.services.tiers import get_tier_limits
from app.db.models import UserModelSelection
from sqlalchemy import select
from app.llm.adapter import llm_completion

router = APIRouter(prefix="/providers", tags=["providers"])


MASKED_KEY = "__MASKED__"


@router.post("/test")
async def test_active_provider(
    payload: ProviderCredentialCreate | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify that the active provider, credential, and selected model work.

    If ``api_key`` is ``__MASKED__`` (sent by the frontend when editing without
    changing the key), the stored encrypted key from the database is used instead.
    """
    config = await get_user_active_provider_config(db, user["sub"])
    if payload is not None:
        api_key = payload.api_key
        # __MASKED__ means "use stored key" — frontend sends this during edit
        if api_key == MASKED_KEY:
            api_key = config.get("api_key")
        config = {
            "provider": payload.provider,
            "model": payload.model or config.get("model"),
            "api_key": api_key,
            "api_base": str(payload.api_base) if payload.api_base else config.get("api_base"),
        }
    response = await llm_completion(
        [{"role": "user", "content": (
            "Reply with ONLY a valid JSON object with these fields: "
            "{\"status\": \"ok\", \"provider\": \"<provider-name>\", "
            "\"model\": \"<model-name>\", \"timestamp\": \"<current-iso-timestamp>\"}. "
            "No explanation, no markdown, no extra text."
        )}],
        provider=config["provider"],
        model=config["model"],
        api_key=config.get("api_key"),
        api_base=config.get("api_base"),
        temperature=0,
        max_tokens=100,
    )
    return {"ok": True, "provider": config["provider"], "model": config["model"], "response": response}


# ── Catalog ─────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=list[ProviderInfo],
    summary="List all available LLM providers",
)
async def list_available_providers() -> list[ProviderInfo]:
    """Return catalog of known providers with metadata for UI."""
    from app.schemas.providers import KNOWN_PROVIDERS

    return KNOWN_PROVIDERS


# ── User's configured providers ─────────────────────────────────────


@router.get(
    "/me",
    response_model=list[ProviderCredentialOut],
    summary="List current user's configured providers",
)
async def list_my_providers(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderCredentialOut]:
    """Return all providers the user has configured (without API keys)."""
    providers = await list_user_providers(db, user["sub"])
    active_config = await get_user_active_provider_config(db, user["sub"])
    limits = get_tier_limits(user.get("tier", "free"))

    model_rows = await db.execute(
        select(UserModelSelection.provider, UserModelSelection.model)
        .where(UserModelSelection.user_id == user["sub"])
    )
    model_map: dict[str, str | None] = {row.provider: row.model for row in model_rows.all()}

    result: list[ProviderCredentialOut] = []
    for p in providers:
        model = model_map.get(p["provider"])
        result.append(
            ProviderCredentialOut(
                provider=p["provider"],
                api_base=p["api_base"],
                model=model,
                has_key=True,
                is_active=p["provider"] == active_config.get("provider"),
                usage_limits=limits,
            )
        )
    return result


@router.get(
    "/me/active",
    response_model=ActiveProviderOut,
    summary="Get current user's active provider configuration",
)
async def get_my_active_provider(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActiveProviderOut:
    """Return the active provider config for the current user."""
    config = await get_user_active_provider_config(db, user["sub"])
    return ActiveProviderOut(
        provider=config["provider"],
        model=config["model"],
        api_base=config["api_base"],
        has_credential=config["api_key"] is not None,
    )


# ── CRUD ────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=ProviderCredentialOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add or update a provider credential",
)
async def create_or_update_provider(
    payload: ProviderCredentialCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderCredentialOut:
    """Store or update an API key for a provider. The key is encrypted at rest.

    Free-tier users:
    - Cannot use ``nvidia_nim``.
    - Can only have ``max_providers=1`` provider configured.
    """
    limits = get_tier_limits(user.get("tier", "free"))

    # Block nvidia_nim for free tier
    if payload.provider == "nvidia_nim" and not limits["allow_nvidia_nim"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="NVIDIA NIM is only available on the Premium plan.",
        )

    # Check max providers count
    existing = await list_user_providers(db, user["sub"])
    existing_for_provider = [p for p in existing if p["provider"] == payload.provider]
    # Only count toward the limit if this is a NEW provider (not an update)
    if not existing_for_provider and len(existing) >= limits["max_providers"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You can only have {limits['max_providers']} provider(s) on your current plan. Upgrade to Premium for more.",
        )

    credential = await set_provider_credential(
        db=db,
        user_id=user["sub"],
        provider=payload.provider,
        api_key=payload.api_key,
        api_base=str(payload.api_base) if payload.api_base else None,
    )
    if payload.model:
        await set_user_model_selection(db, user["sub"], payload.provider, payload.model)
    return ProviderCredentialOut(
        provider=credential.provider,
        api_base=credential.api_base,
        model=payload.model,
        has_key=True,
        is_active=False,
    )


@router.patch(
    "/{provider}",
    response_model=ProviderCredentialOut,
    summary="Update a provider credential (partial)",
)
async def update_provider(
    provider: str,
    payload: ProviderCredentialUpdate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderCredentialOut:
    """Update API key, base URL, or model for an existing provider."""
    existing = await get_provider_credential(db, user["sub"], provider)
    if not existing:
        from app.exceptions import NotFoundError

        raise NotFoundError(f"Provider '{provider}' not configured")

    # Only update fields that are provided; api_key=None keeps existing
    credential = await set_provider_credential(
        db=db,
        user_id=user["sub"],
        provider=provider,
        api_key=payload.api_key,
        api_base=str(payload.api_base) if payload.api_base else None,
    )
    if payload.model:
        await set_user_model_selection(db, user["sub"], provider, payload.model)
    # Fetch current model to return
    current_model = payload.model or await get_user_model_selection(db, user["sub"], provider)
    return ProviderCredentialOut(
        provider=credential.provider,
        api_base=credential.api_base,
        model=current_model,
        has_key=True,
        is_active=False,
    )


@router.delete(
    "/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a provider credential",
)
async def delete_provider(
    provider: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a provider credential for the current user."""
    deleted = await delete_provider_credential(db, user["sub"], provider)
    if not deleted:
        return None


# ── Active provider ─────────────────────────────────────────────────


@router.put(
    "/active",
    response_model=ActiveProviderOut,
    summary="Set the active LLM provider",
)
async def set_active_provider(
    payload: SetActiveProvider,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActiveProviderOut:
    """Set which provider the user wants to use for LLM calls."""
    # Verify the provider is configured
    credential = await get_provider_credential(db, user["sub"], payload.provider)
    if not credential:
        from app.exceptions import NotFoundError

        raise NotFoundError(f"Provider '{payload.provider}' not configured. Add it first.")

    await set_user_active_provider(db, user["sub"], payload.provider)
    config = await get_user_active_provider_config(db, user["sub"])
    return ActiveProviderOut(
        provider=config["provider"],
        model=config["model"],
        api_base=config["api_base"],
        has_credential=config["api_key"] is not None,
    )


# ── Model listing & selection ──────────────────────────────────────


@router.get(
    "/me/model",
    response_model=ActiveModelOut,
    summary="Get the user's selected model for their active provider",
)
async def get_my_active_model(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActiveModelOut:
    """Return the model the user has selected for their currently active provider."""
    from app.services.provider_credentials import get_user_active_provider_config

    config = await get_user_active_provider_config(db, user["sub"])
    provider = config["provider"]
    model = await get_user_model_selection(db, user["sub"], provider)
    return ActiveModelOut(provider=provider, model=model)


@router.get(
    "/{provider}/models",
    response_model=ModelListOut,
    summary="List models available to the user for a provider",
)
async def list_models_for_provider(
    provider: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModelListOut:
    """List the models the user can use with the given provider.

    For OpenAI, NVIDIA NIM, LM Studio and Ollama the list is fetched live from
    the provider using the user's stored credential.  For Anthropic (no public
    list endpoint) a curated static list is returned with `source: "static"`.
    """
    return await list_provider_models(db, user["sub"], provider)


@router.post("/{provider}/models", response_model=ModelListOut)
async def preview_provider_models(
    provider: str,
    payload: ProviderCredentialCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModelListOut:
    """Load models using unsaved form credentials."""
    return await list_provider_models(
        db, user["sub"], provider,
        api_key=payload.api_key,
        api_base=str(payload.api_base) if payload.api_base else None,
    )


@router.put(
    "/{provider}/model",
    response_model=ActiveModelOut,
    summary="Set the user's selected model for a provider",
)
async def set_model_for_provider(
    provider: str,
    payload: SetModelSelection,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActiveModelOut:
    """Persist the user's chosen model for the given provider."""
    await set_user_model_selection(db, user["sub"], provider, payload.model)
    return ActiveModelOut(provider=provider, model=payload.model)
