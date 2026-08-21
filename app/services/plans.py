"""Database-backed subscription plan catalog."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan
from app.services.credit_costs import CREDIT_ACTION_CATALOG, get_effective_costs

DEFAULT_CREDIT_COSTS: dict[str, int] = {s.key: s.default_cost for s in CREDIT_ACTION_CATALOG}


class NoPlansConfiguredError(RuntimeError):
    """Raised when the database has no active plans configured."""


async def get_plan(db: AsyncSession, key: str) -> Plan | None:
    result = await db.execute(select(Plan).where(Plan.key == key))
    return result.scalar_one_or_none()


async def get_active_plans(db: AsyncSession) -> list[Plan]:
    result = await db.execute(select(Plan).where(Plan.is_active == True).order_by(Plan.sort_order.asc()))  # noqa: E712
    return list(result.scalars().all())


async def get_all_plans(db: AsyncSession) -> list[Plan]:
    result = await db.execute(select(Plan).order_by(Plan.sort_order.asc()))
    return list(result.scalars().all())


async def upsert_plan(db: AsyncSession, data: dict[str, Any]) -> Plan:
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
    plan = await get_plan(db, key)
    if plan is None:
        return False
    await db.delete(plan)
    await db.flush()
    return True


async def get_credit_costs(db: AsyncSession) -> dict[str, int]:
    return await get_effective_costs(db)


async def get_whatsapp_number() -> str:
    import os

    return os.getenv("WHATSAPP_NUMBER", "").strip()


async def build_catalog(db: AsyncSession) -> dict[str, Any]:
    """Build the catalog exclusively from active database rows."""
    from app.services.topups import get_topup_packs

    plans = await get_active_plans(db)
    if not plans:
        raise NoPlansConfiguredError("No active plans configured. Apply the plans bootstrap migration.")
    last_updated = max((p.updated_at for p in plans if p.updated_at), default=None)
    if last_updated is not None and last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=UTC)
    version = int(last_updated.timestamp()) if last_updated else 1
    return {
        "plans": plans,
        "credit_costs": await get_credit_costs(db),
        "topup_packs": await get_topup_packs(db),
        "whatsapp_number": await get_whatsapp_number(),
        "currency": "USD",
        "last_updated": last_updated,
        "version": version,
    }
