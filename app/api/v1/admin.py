"""Admin router — user management + global provider configuration.

Only accessible to users with ``role == "admin"``.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_locale
from app.core.i18n.locale import t
from app.db.models import User
from app.llm.adapter import llm_completion
from app.schemas.auth import AdminUserListOut, AdminUserOut, AdminUserUpdate
from app.schemas.providers import (
    AdminProviderConfigOut,
    AdminProviderConfigUpdate,
    KNOWN_PROVIDERS,
    ModelListOut,
    ProviderInfo,
)
from app.services.provider_config import (
    MASKED_KEY,
    clear_global_provider_config,
    get_active_provider_config,
    get_global_provider_config_out,
    set_global_provider_config,
)
from app.services.provider_models import list_provider_models

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: ensure the current user has admin role."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


@router.get("/users", response_model=AdminUserListOut)
async def list_users(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    search: str = "",
    role: str = "",
    tier: str = "",
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 5,
):
    """List registered users with server-side pagination. Admin only.

    Only the current page is loaded from the DB (default 5 rows), with
    optional filters (search/role/tier) and sorting applied in SQL.
    """
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    # Shared filter conditions (used by both the page query and the count).
    conditions = []
    if search.strip():
        like = f"%{search.strip().lower()}%"
        conditions.append(
            func.lower(User.email).like(like) | func.lower(func.coalesce(User.full_name, "")).like(like)
        )
    if role in ("admin", "client"):
        conditions.append(User.role == role)
    if tier in ("free", "premium"):
        conditions.append(User.tier == tier)

    query = select(User).where(*conditions)

    # Whitelist sortable columns — anything else falls back to created_at.
    sortable = {"full_name", "email", "role", "tier", "created_at"}
    sort_col = getattr(User, sort, User.created_at) if sort in sortable else User.created_at
    if order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    total = (await db.execute(select(func.count()).select_from(User).where(*conditions))).scalar_one()

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    users = result.scalars().all()

    # Global stats (unfiltered) for the dashboard cards.
    stats_total = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    stats_admins = (
        await db.execute(select(func.count()).select_from(User).where(User.role == "admin"))
    ).scalar_one()
    stats_premium = (
        await db.execute(select(func.count()).select_from(User).where(User.tier == "premium"))
    ).scalar_one()

    return AdminUserListOut(
        items=users,
        total=total,
        page=page,
        page_size=page_size,
        stats={"total": stats_total, "admins": stats_admins, "premium": stats_premium},
    )


@router.get("/users/{user_id}", response_model=AdminUserOut)
async def get_user(
    user_id: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Get a single user by ID. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.not_found", locale),
        )
    return user


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Update a user's tier or role. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.not_found", locale),
        )

    if payload.tier is not None:
        if payload.tier not in ("free", "premium"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tier must be 'free' or 'premium'",
            )
        user.tier = payload.tier

    if payload.role is not None:
        if payload.role not in ("admin", "client"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Role must be 'admin' or 'client'",
            )
        user.role = payload.role

    await db.flush()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_200_OK)
async def delete_user(
    user_id: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Delete a user account. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.not_found", locale),
        )

    await db.delete(user)
    await db.flush()
    return {"message": "User deleted"}


# ── Global provider config (Phase 2) ────────────────────────────────


@router.get("/providers", response_model=AdminProviderConfigOut)
async def get_provider_config(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminProviderConfigOut:
    """Return the current global provider configuration. Admin only."""
    config = await get_global_provider_config_out(db)
    return AdminProviderConfigOut(**config)


@router.get("/providers/catalog", response_model=list[ProviderInfo])
async def list_global_providers_catalog(
    admin: dict = Depends(require_admin),
) -> list[ProviderInfo]:
    """Return catalog of known providers with metadata for the admin UI. Admin only."""
    from app.schemas.providers import KNOWN_PROVIDERS

    return KNOWN_PROVIDERS


@router.put("/providers", response_model=AdminProviderConfigOut)
async def put_provider_config(
    payload: AdminProviderConfigUpdate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminProviderConfigOut:
    """Set the global provider configuration. Admin only.

    ``api_key`` of ``__MASKED__`` keeps the stored encrypted key; an empty
    string clears it.
    """
    known = {p.name for p in KNOWN_PROVIDERS}
    if payload.provider not in known:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown provider '{payload.provider}'",
        )

    try:
        row = await set_global_provider_config(
            db,
            provider=payload.provider,
            api_key=payload.api_key,
            api_base=str(payload.api_base) if payload.api_base else None,
            model=payload.model,
            updated_by=admin["sub"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    await db.commit()
    await db.refresh(row)
    return await get_global_provider_config_out(db)


@router.post("/providers/test")
async def test_provider_config(
    payload: AdminProviderConfigUpdate | None = None,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify that the *saved* global provider config works. Admin only.

    If ``payload`` is provided, the test runs against those values without
    persisting them (useful to validate before saving).  ``api_key`` of
    ``__MASKED__`` uses the stored key.
    """
    if payload is not None:
        stored = await get_active_provider_config(db)
        api_key = payload.api_key
        if api_key == MASKED_KEY:
            api_key = stored.get("api_key")
        config = {
            "provider": payload.provider,
            "model": payload.model or stored.get("model"),
            "api_key": api_key,
            "api_base": str(payload.api_base) if payload.api_base else stored.get("api_base"),
        }
    else:
        config = await get_active_provider_config(db)

    from app.core.settings import get_settings

    settings = get_settings()
    api_key = config.get("api_key") or getattr(settings, f"{config['provider']}_api_key", None)
    if api_key is None and config["provider"] not in ("lm_studio", "ollama"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No API key configured. Set it first via PUT /admin/providers.",
        )

    try:
        response = await llm_completion(
            [{"role": "user", "content": (
                "Reply with ONLY a valid JSON object with these fields: "
                "{\"status\": \"ok\", \"provider\": \"<provider-name>\", "
                "\"model\": \"<model-name>\", \"timestamp\": \"<current-iso-timestamp>\"}. "
                "No explanation, no markdown, no extra text."
            )}],
            provider=config["provider"],
            model=config["model"] or "claude-sonnet-4-20250514",
            api_key=api_key,
            api_base=config.get("api_base"),
            temperature=0,
            max_tokens=100,
        )
    except Exception as exc:
        return {"ok": False, "provider": config["provider"], "model": config.get("model"), "error": str(exc)}
    return {"ok": True, "provider": config["provider"], "model": config.get("model"), "response": response}


@router.post("/providers/{provider}/models", response_model=ModelListOut)
async def preview_global_provider_models(
    provider: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ModelListOut:
    """List models for a provider using the global config. Admin only."""
    return await list_provider_models(db, provider)


@router.delete("/providers", response_model=AdminProviderConfigOut)
async def clear_provider_config(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminProviderConfigOut:
    """Clear the global provider config (falls back to .env). Admin only."""
    await clear_global_provider_config(db)
    await db.commit()
    return await get_global_provider_config_out(db)