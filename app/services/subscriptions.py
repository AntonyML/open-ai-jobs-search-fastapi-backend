"""Subscription service — lifecycle, correlation IDs and feature gating.

Plans are read from the DB (``plans`` table).  Activating a subscription:

1. Creates (or renews) a ``UserSubscription`` row with a fresh correlation ID.
2. Sets ``users.tier`` to the plan key so existing JWT/pipeline logic works.
3. Links/creates the user's credit account for the new period.

The admin account always has an auto-renewing ``max`` subscription so the
owner never needs a separate "god" plan.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CreditAccount, User, UserSubscription
from app.services.plans import get_plan

logger = logging.getLogger(__name__)

PERIOD_DAYS = {"monthly": 30, "yearly": 365}


def _period_bounds(cycle: str, start: datetime | None = None) -> tuple[datetime, datetime]:
    """Return (period_start, period_end) for a billing cycle."""
    now = start or datetime.now(UTC)
    days = PERIOD_DAYS.get(cycle, 30)
    return now, now + timedelta(days=days)


async def _get_active_subscription(
    db: AsyncSession, user_id: str
) -> UserSubscription | None:
    """The user's latest active subscription row (any plan)."""
    result = await db.execute(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
        )
        .order_by(UserSubscription.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _link_credit_account(
    db: AsyncSession,
    user_id: str,
    subscription: UserSubscription,
) -> None:
    """Point the user's credit account at this subscription period."""
    result = await db.execute(
        select(CreditAccount).where(CreditAccount.user_id == user_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        account = CreditAccount(
            user_id=user_id,
            subscription_id=subscription.id,
            balance=0,
            last_refill_at=datetime.now(UTC) - timedelta(days=7),
            quota_day_reset_at=datetime.now(UTC),
            quota_week_reset_at=datetime.now(UTC),
        )
        db.add(account)
    else:
        # New period: clear the refill anchor so ``refill_if_due`` grants the
        # new period's credits (credits expire with the previous period).
        account.subscription_id = subscription.id
        account.last_refill_at = None
    await db.flush()


async def activate_subscription(
    db: AsyncSession,
    user: User,
    plan_key: str,
    *,
    billing_cycle: str = "monthly",
    source: str = "purchase",
    auto_renew: bool = False,
    price_paid: float = 0.0,
    note: str | None = None,
) -> UserSubscription:
    """Activate (or renew) a subscription for a user.

    Only one active subscription per user: activating a new plan supersedes
    the previous one (the old row is marked ``cancelled``).
    """
    plan = await get_plan(db, plan_key)
    if plan is None:
        raise ValueError(f"Unknown plan key '{plan_key}'")
    if not plan.is_active:
        raise ValueError(f"Plan '{plan_key}' is not active")

    # Cancel any previous active subscription.
    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.user_id == user.id,
            UserSubscription.status == "active",
        )
    )
    for old in result.scalars().all():
        old.status = "cancelled"

    period_start, period_end = _period_bounds(billing_cycle)
    subscription = UserSubscription(
        user_id=user.id,
        plan_key=plan_key,
        correlation_id=uuid.uuid4().hex,
        period_start=period_start,
        period_end=period_end,
        status="active",
        source=source,
        auto_renew=auto_renew,
        price_paid=price_paid,
    )
    db.add(subscription)
    await db.flush()

    # Sync the user's tier to the plan key (drives the legacy tier checks).
    user.tier = plan_key
    await _link_credit_account(db, user.id, subscription)
    await db.flush()

    logger.info(
        "Subscription activated: user=%s plan=%s cycle=%s cid=%s note=%s",
        user.id,
        plan_key,
        billing_cycle,
        subscription.correlation_id,
        note or "",
    )
    return subscription


async def ensure_admin_subscription(db: AsyncSession, user_id: str) -> None:
    """Guarantee the admin has an active auto-renewing ``max`` subscription.

    Idempotent: if the user already has an active subscription the plan key
    is just synced to ``max`` (admin always sees the full pipeline).
    """
    existing = await _get_active_subscription(db, user_id)
    if existing is not None:
        return

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return

    try:
        await activate_subscription(
            db,
            user,
            "max",
            billing_cycle="monthly",
            source="admin",
            auto_renew=True,
            note="Auto-renewing admin subscription",
        )
    except ValueError:
        logger.warning("Admin max subscription could not be created (plan missing?)")


async def renew_subscription(db: AsyncSession, subscription: UserSubscription) -> None:
    """Renew an existing subscription into a new period (auto-renew).

    The billing cycle is inferred from the previous period length so the
    admin's auto-renewing subscription keeps its original cadence.
    """
    now = datetime.now(UTC)
    if subscription.period_start is not None and subscription.period_end is not None:
        span_days = (subscription.period_end - subscription.period_start).days or 30
        cycle = "yearly" if span_days >= 360 else "monthly"
    else:
        cycle = "monthly"
    period_start, period_end = _period_bounds(cycle, now)
    subscription.period_start = period_start
    subscription.period_end = period_end
    subscription.status = "active"
    subscription.correlation_id = uuid.uuid4().hex
    subscription.auto_renew = True
    # Clear the refill anchor so the credits for the new period are granted
    # (the previous period's remaining credits expire).
    result = await db.execute(
        select(CreditAccount).where(CreditAccount.user_id == subscription.user_id)
    )
    account = result.scalar_one_or_none()
    if account is not None:
        account.last_refill_at = None
    await db.flush()


async def expire_subscription(db: AsyncSession, subscription: UserSubscription) -> None:
    """Mark a subscription as expired and reset the user's tier to free."""
    subscription.status = "expired"
    result = await db.execute(select(User).where(User.id == subscription.user_id))
    user = result.scalar_one_or_none()
    if user is not None and user.tier == subscription.plan_key:
        user.tier = "free"
    await db.flush()
    logger.info(
        "Subscription expired: user=%s plan=%s",
        subscription.user_id,
        subscription.plan_key,
    )


def _normalize(dt: datetime | None) -> datetime:
    """Timezone-aware UTC (SQLite returns naive datetimes)."""
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def process_expired_subscriptions(db: AsyncSession) -> int:
    """Find expired active subscriptions and expire them (auto-renew renews).

    Called lazily by the billing status endpoint so no scheduler is needed.
    Returns how many subscriptions changed state.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.status == "active",
            UserSubscription.period_end.isnot(None),
        )
    )
    changed = 0
    for sub in result.scalars().all():
        if _normalize(sub.period_end) >= now:
            continue
        if sub.auto_renew:
            await renew_subscription(db, sub)
        else:
            await expire_subscription(db, sub)
        changed += 1
    if changed:
        await db.flush()
    return changed


# ── Gating helpers ────────────────────────────────────────────────────


async def get_user_access(db: AsyncSession, user: dict | User) -> dict:
    """Resolve the user's effective access: subscription, plan, features.

    Returns a dict with plan_key, features, active subscription, quotas and
    the plan's credit allowance so endpoints and the frontend can gate on it.
    """
    user_id = user.id if isinstance(user, User) else user["sub"]
    role = user.role if isinstance(user, User) else user.get("role", "client")
    is_admin = role == "admin"

    sub = await _get_active_subscription(db, user_id)
    plan = None
    if sub is not None:
        plan = await get_plan(db, sub.plan_key)

    features: list[str] = []
    daily_quota = 0
    weekly_quota = 0
    if plan is not None:
        features = list(plan.features or [])
        daily_quota = plan.daily_quota
        weekly_quota = plan.weekly_quota
    if is_admin:
        # Admin always has full access even before a subscription exists.
        for f in ("cv_base", "cv_adapted", "pipeline", "expand", "upskill"):
            if f not in features:
                features.append(f)
        if daily_quota == 0:
            daily_quota = 1000
        if weekly_quota == 0:
            weekly_quota = 10000

    return {
        "is_admin": is_admin,
        "plan_key": sub.plan_key if sub else None,
        "subscription": sub,
        "plan": plan,
        "features": features,
        "daily_quota": daily_quota,
        "weekly_quota": weekly_quota,
    }


async def can_use_feature(db: AsyncSession, user: dict | User, feature: str) -> bool:
    """True if the user's current plan grants ``feature``."""
    access = await get_user_access(db, user)
    return feature in access["features"]
