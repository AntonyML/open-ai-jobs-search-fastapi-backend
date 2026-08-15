"""Plan catalog service.

Plans are stored in the DB (``plans`` table) so the admin can add, edit or
disable tiers without touching code.  This module owns the default seed
catalog (free / pro / max).

Credit-cost calibration moved to the typed ``credit_cost_config`` table
via ``app.services.credit_costs`` (plan.md §8.2) — the legacy
``app_config['credit_costs']`` JSON blob no longer exists after the
migration.  ``get_credit_costs`` is kept here as a thin delegate so callers
(catalog, access gate) don't change; all writes go through the strict
``credit_costs.set_effective_costs`` (admin API only).

Current model (confirmed with the owner):

- **free**   — 2 credits/week (enough to test 1 base CV + 1 adapted CV),
               pipeline locked. Credits refill weekly, never accumulate.
- **pro**    — credits per period ($19.99/mo or $199/yr). CV builder only.
- **max**    — pipeline + everything ($59.99/mo). Still rate-limited by
               daily/weekly quotas — nothing is truly unlimited.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan
from app.services.credit_costs import CREDIT_ACTION_CATALOG, get_effective_costs

# Kept for backwards compatibility with imports; the canonical catalog now
# lives in app.services.credit_costs (plan.md §8.2).
DEFAULT_CREDIT_COSTS: dict[str, int] = {
    s.key: s.default_cost for s in CREDIT_ACTION_CATALOG
}


# ── Default catalog (seeded on first access if the table is empty) ────

DEFAULT_PLANS: list[dict[str, Any]] = [
    {
        "key": "free",
        "name": "Free",
        "description": "Prueba el generador de CV: 2 créditos por semana, sin pipeline.",
        "price_monthly_usd": 0.0,
        "price_yearly_usd": 0.0,
        "credits_per_period": 2,
        "refill_cadence": "weekly",
        "refill_weekday": 0,
        "daily_quota": 0,
        "weekly_quota": 0,
        "features": ["cv_base", "cv_adapted"],
        "is_active": True,
        "sort_order": 10,
    },
    {
        "key": "pro",
        "name": "Pro",
        "description": "Créditos para seguir generando y adaptando tu CV.",
        "price_monthly_usd": 19.99,
        "price_yearly_usd": 199.0,
        "credits_per_period": 100,
        "refill_cadence": "period",
        "refill_weekday": 0,
        "daily_quota": 0,
        "weekly_quota": 0,
        "features": ["cv_base", "cv_adapted"],
        "is_active": True,
        "sort_order": 20,
    },
    {
        "key": "max",
        "name": "Max",
        "description": (
            "Todo el pipeline de búsqueda de empleo + CV ilimitado "
            "con cuotas diarias/semanales."
        ),
        "price_monthly_usd": 59.99,
        "price_yearly_usd": 599.0,
        "credits_per_period": 500,
        "refill_cadence": "period",
        "refill_weekday": 0,
        "daily_quota": 20,
        "weekly_quota": 80,
        "features": ["cv_base", "cv_adapted", "pipeline", "expand", "upskill"],
        "is_active": True,
        "sort_order": 30,
    },
]


async def seed_default_plans(db: AsyncSession) -> None:
    """Insert the default catalog if the ``plans`` table is empty.

    Called lazily from the catalog endpoints so no startup hook is needed.
    """
    result = await db.execute(select(Plan.id).limit(1))
    if result.scalar_one_or_none() is not None:
        return
    for data in DEFAULT_PLANS:
        db.add(Plan(**data))
    await db.flush()


async def get_plan(db: AsyncSession, key: str) -> Plan | None:
    """Fetch a plan by key (None if missing)."""
    result = await db.execute(select(Plan).where(Plan.key == key))
    return result.scalar_one_or_none()


async def get_active_plans(db: AsyncSession) -> list[Plan]:
    """All active plans ordered by ``sort_order``."""
    await seed_default_plans(db)
    result = await db.execute(
        select(Plan).where(Plan.is_active == True).order_by(Plan.sort_order.asc())  # noqa: E712
    )
    return list(result.scalars().all())


async def get_all_plans(db: AsyncSession) -> list[Plan]:
    """Every plan (active or not), for the admin panel."""
    await seed_default_plans(db)
    result = await db.execute(select(Plan).order_by(Plan.sort_order.asc()))
    return list(result.scalars().all())


async def upsert_plan(db: AsyncSession, data: dict[str, Any]) -> Plan:
    """Create or update a plan by key (admin panel)."""
    plan = await get_plan(db, data["key"])
    if plan is None:
        plan = Plan(**data)
        db.add(plan)
    else:
        for field, value in data.items():
            setattr(plan, field, value)
    await db.flush()
    await db.refresh(plan)
    return plan


async def delete_plan(db: AsyncSession, key: str) -> bool:
    """Delete a plan by key. Returns False if it did not exist."""
    plan = await get_plan(db, key)
    if plan is None:
        return False
    await db.delete(plan)
    await db.flush()
    return True


# ── Credit-cost configuration (admin-editable singleton) ──────────────


async def get_credit_costs(db: AsyncSession) -> dict[str, int]:
    """Effective credit cost per action (typed table, plan.md §8.2)."""
    return await get_effective_costs(db)


async def get_whatsapp_number() -> str:
    """Return the admin WhatsApp number for the manual payment flow.

    Configurable via ``WHATSAPP_NUMBER`` env var; falls back to empty
    (frontend hides the WhatsApp CTA when unset).
    """
    import os

    return os.getenv("WHATSAPP_NUMBER", "").strip()


async def build_catalog(db: AsyncSession) -> dict[str, Any]:
    """Assemble the full product catalog payload.

    Shared by the authenticated ``/billing/catalog`` endpoint and the public
    ``/public/catalog`` endpoint so both return the exact same shape.
    """
    # Lazy import: topups imports get_plan from this module, so a module-level
    # import here would create a circular dependency.
    from app.services.topups import get_topup_packs

    plans = await get_active_plans(db)
    last_updated = max((p.updated_at for p in plans), default=None)
    return {
        "plans": plans,
        "credit_costs": await get_credit_costs(db),
        "topup_packs": await get_topup_packs(db),
        "whatsapp_number": await get_whatsapp_number(),
        "currency": "USD",
        "last_updated": last_updated,
    }
