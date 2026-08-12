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
from app.schemas.billing import (
    AdminCreditAdjust,
    AdminSubscriptionCreate,
    CreditTransactionOut,
    PlanAdminOut,
    PlanUpsert,
    SubscriptionAdminOut,
    UserSubscriptionOut,
)
from app.services import credits
from app.services.notifications import (
    get_notification_ttl_days,
    mark_purchase_requests_read,
    set_notification_ttl_days,
)
from app.services.plans import (
    delete_plan,
    get_all_plans,
    get_credit_costs,
    set_credit_costs,
    upsert_plan,
)
from app.services.subscriptions import activate_subscription
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
    if tier:
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
        # Accept any plan key from the DB catalog (free / pro / max + future).
        from app.services.plans import get_plan as _get_plan

        if await _get_plan(db, payload.tier) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown plan key '{payload.tier}'",
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


# ── Plans & credit configuration (admin) ─────────────────────────────


@router.get("/plans", response_model=list[PlanAdminOut])
async def list_plans(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all plans (active + inactive). Admin only."""
    return await get_all_plans(db)


@router.put("/plans/{plan_key}", response_model=PlanAdminOut)
async def put_plan(
    plan_key: str,
    payload: PlanUpsert,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a plan by key. Admin only."""
    data = payload.model_dump()
    data["key"] = plan_key
    plan = await upsert_plan(db, data)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.delete("/plans/{plan_key}", status_code=status.HTTP_200_OK)
async def remove_plan(
    plan_key: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a plan. Admin only."""
    deleted = await delete_plan(db, plan_key)
    await db.commit()
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found",
        )
    return {"message": "Plan deleted"}


@router.get("/credit-costs")
async def get_admin_credit_costs(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return the current credit cost per action. Admin only."""
    return await get_credit_costs(db)


@router.put("/credit-costs")
async def put_admin_credit_costs(
    payload: dict,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update the credit cost per action (calibration). Admin only."""
    costs = await set_credit_costs(db, payload)
    await db.commit()
    return costs


# ── Notification retention TTL (admin) ────────────────────────────────


@router.get("/notification-ttl")
async def get_admin_notification_ttl(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return the current notification retention in days. Admin only."""
    return {"days": await get_notification_ttl_days(db)}


@router.put("/notification-ttl")
async def put_admin_notification_ttl(
    payload: dict,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update the notification retention in days. Admin only.

    Accepts ``{"days": N}`` with N >= 1; anything else falls back to the
    default when read.
    """
    raw = payload.get("days")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="days must be a positive integer",
        )
    days = await set_notification_ttl_days(db, raw)
    await db.commit()
    return {"days": days}


# ── Credits & subscriptions (admin) ──────────────────────────────────


@router.post("/credits/adjust")
async def adjust_user_credits(
    payload: AdminCreditAdjust,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually add/remove credits for a user. Admin only."""
    result = await db.execute(select(User).where(User.id == payload.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    account = await credits.adjust_credits(
        db,
        payload.user_id,
        payload.delta,
        description=payload.reason or "Manual admin adjustment",
    )
    await db.commit()
    return {"user_id": payload.user_id, "balance": account.balance}


@router.post("/subscriptions", response_model=UserSubscriptionOut)
async def create_subscription(
    payload: AdminSubscriptionCreate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Activate a subscription for a user (manual payment flow). Admin only."""
    result = await db.execute(select(User).where(User.id == payload.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    try:
        sub = await activate_subscription(
            db,
            user,
            payload.plan_key,
            billing_cycle=payload.billing_cycle,
            source="admin",
            auto_renew=payload.auto_renew,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    # Auto-close the admin's pending purchase notifications for this user.
    await mark_purchase_requests_read(db, admin["sub"], payload.user_id)
    await db.commit()
    await db.refresh(sub)
    return sub


@router.get("/subscriptions", response_model=list[SubscriptionAdminOut])
async def list_subscriptions(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    plan: str = "",
    status_filter: str = "",
    limit: int = 100,
):
    """List user subscriptions with optional filters. Admin only."""
    from app.db.models import UserSubscription

    query = select(UserSubscription).order_by(UserSubscription.created_at.desc()).limit(min(max(limit, 1), 500))
    if plan:
        query = query.where(UserSubscription.plan_key == plan)
    if status_filter:
        query = query.where(UserSubscription.status == status_filter)
    result = await db.execute(query)
    subs = list(result.scalars().all())

    # Enrich with user emails (single query, no N+1).
    user_ids = {s.user_id for s in subs}
    emails: dict[str, str] = {}
    if user_ids:
        rows = await db.execute(select(User.id, User.email).where(User.id.in_(user_ids)))
        emails = {row[0]: row[1] for row in rows.all()}

    return [
        SubscriptionAdminOut(
            **UserSubscriptionOut.model_validate(s).model_dump(),
            user_id=s.user_id,
            user_email=emails.get(s.user_id, ""),
        )
        for s in subs
    ]


@router.get("/users/{user_id}/transactions", response_model=list[CreditTransactionOut])
async def user_credit_transactions(
    user_id: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Credit ledger for a user. Admin only."""
    rows = await credits.get_recent_transactions(db, user_id, limit=100)
    return [CreditTransactionOut.model_validate(x) for x in rows]
