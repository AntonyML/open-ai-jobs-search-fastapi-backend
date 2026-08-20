"""Top-up packs service — prepaid credit packs (manual payment flow).

Top-ups are fixed packs (locked to exactly 2) stored in ``app_config``
under ``TOPUP_PACKS_CONFIG_KEY`` following the ``credit_costs`` singleton
pattern.  The admin API can edit them, but the shape is validated: exactly
2 packs, each with ``price_usd`` > 0 and ``credits`` > 0.

Policy (confirmed with the owner, plan.md §9.1):
  - top-ups are only offered on **paid plans** (pro/max) with an active
    subscription — ``apply_topup`` enforces that (free users would lose
    the credits at the next weekly refill);
  - credits never accumulate: at the next period turnover the balance
    resets to the plan allowance, so a top-up is only usable within the
    current billing period (the modal warns the user explicitly).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppConfig, Plan, UserSubscription
from app.services.credits import adjust_credits
from app.services.plans import get_plan

# Config key for the top-up packs singleton (upsert on this key).
TOPUP_PACKS_CONFIG_KEY = "topup_packs"

# Locked to exactly 2 packs.  ``price_usd`` = manual payment (SINPE/WhatsApp/email).
DEFAULT_TOPUP_PACKS: list[dict[str, Any]] = [
    {"price_usd": 9.99, "credits": 50},
    {"price_usd": 19.99, "credits": 120},
]

# The catalog is intentionally fixed at 2 packs — no more, no less.
MAX_TOPUP_PACKS = 2


class TopupNotAllowedError(Exception):
    """Raised when a user cannot top up (no active subscription on a paid plan).

    ``code`` is the machine-readable key the API maps to a structured
    ``detail`` (e.g. ``topup_requires_plan``) so the frontend can show the
    right CTA (upgrade instead of top-up).
    """

    def __init__(
        self,
        code: str = "topup_requires_plan",
        message: str = "Top-ups require an active paid plan",
    ):
        self.code = code
        super().__init__(message)


def _valid_pack(pack: Any) -> bool:
    """A pack is valid when it has a positive price and positive credits."""
    if not isinstance(pack, dict):
        return False
    price = pack.get("price_usd")
    credits = pack.get("credits")
    return (
        isinstance(price, int | float)
        and not isinstance(price, bool)
        and price > 0
        and isinstance(credits, int)
        and not isinstance(credits, bool)
        and credits > 0
    )


async def get_topup_packs(db: AsyncSession) -> list[dict[str, Any]]:
    """Return the effective top-up packs (exactly 2, validated).

    Falls back to ``DEFAULT_TOPUP_PACKS`` when the stored value is missing
    or does not validate (same defensive pattern as ``get_credit_costs``).
    """
    result = await db.execute(select(AppConfig).where(AppConfig.key == TOPUP_PACKS_CONFIG_KEY))
    row = result.scalar_one_or_none()
    stored = (row.value if row is not None else None) or []
    if isinstance(stored, list) and len(stored) == MAX_TOPUP_PACKS and all(_valid_pack(p) for p in stored):
        return stored
    return list(DEFAULT_TOPUP_PACKS)


async def set_topup_packs(db: AsyncSession, packs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist the top-up packs (admin panel).

    Raises:
        ValueError: when the packs are not exactly ``MAX_TOPUP_PACKS`` valid packs.
    """
    if not isinstance(packs, list) or len(packs) != MAX_TOPUP_PACKS:
        raise ValueError(f"Exactly {MAX_TOPUP_PACKS} top-up packs are required")
    cleaned = []
    for pack in packs:
        if not _valid_pack(pack):
            raise ValueError("Each top-up pack needs price_usd > 0 and credits > 0")
        cleaned.append(
            {
                "price_usd": float(pack["price_usd"]),
                "credits": int(pack["credits"]),
            }
        )
    result = await db.execute(select(AppConfig).where(AppConfig.key == TOPUP_PACKS_CONFIG_KEY))
    row = result.scalar_one_or_none()
    if row is None:
        row = AppConfig(key=TOPUP_PACKS_CONFIG_KEY, value=cleaned)
        db.add(row)
    else:
        row.value = cleaned
    await db.flush()
    return await get_topup_packs(db)


# ── Applying an approved top-up ──────────────────────────────────────


def is_paid_plan(plan: Plan) -> bool:
    """A plan is "paid" when it charges a price on either billing cycle.

    The seeded ``free`` plan has 0.0/0.0, so ``price > 0`` cleanly separates
    the free tier from every monetizable plan (pro/max + future tiers).
    """
    return (plan.price_monthly_usd or 0) > 0 or (plan.price_yearly_usd or 0) > 0


async def get_paid_subscription(db: AsyncSession, user_id: str) -> tuple[UserSubscription | None, Plan | None]:
    """Return (subscription, plan) for the user's latest *paid* active sub.

    ``(None, None)`` when the user has no active subscription or the active
    plan is free — in both cases top-ups are not allowed.
    """
    result = await db.execute(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
        )
        .order_by(UserSubscription.created_at.desc())
        .limit(1)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        return None, None
    plan = await get_plan(db, subscription.plan_key)
    if plan is None or not plan.is_active or not is_paid_plan(plan):
        return None, None
    return subscription, plan


async def apply_topup(
    db: AsyncSession,
    user_id: str,
    pack: dict[str, Any],
    *,
    correlation_id: str | None = None,
) -> Any:
    """Add an approved top-up pack's credits to the user's balance.

    Called by the admin panel when a pending ``topup_request`` is approved:
    the admin has confirmed the manual payment, so the credits land on the
    ledger with ``action="topup"`` (a positive delta, like a purchase).

    Enforces the paid-plan rule at apply time too: the user must still hold
    an active subscription on a paid plan — if the plan lapsed between the
    request and the approval, the top-up is rejected with
    ``TopupNotAllowedError`` instead of granting credits the refill would
    immediately wipe.

    Raises:
        ValueError: when ``pack`` is not a valid top-up pack.
        TopupNotAllowedError: when the user has no active paid subscription.
    """
    if not _valid_pack(pack):
        raise ValueError("Invalid top-up pack")
    credits_ = int(pack["credits"])
    price_usd = float(pack["price_usd"])

    subscription, _plan = await get_paid_subscription(db, user_id)
    if subscription is None:
        raise TopupNotAllowedError(message="Top-ups require an active subscription on a paid plan")

    return await adjust_credits(
        db,
        user_id,
        credits_,
        action="topup",
        correlation_id=correlation_id,
        description=f"Top-up: {credits_} credits (${price_usd:.2f})",
    )
