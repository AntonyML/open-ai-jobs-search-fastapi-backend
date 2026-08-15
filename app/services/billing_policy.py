"""Billing policy service — refund eligibility and prorated upgrades.

Policies live in ``app_config`` under ``BILLING_POLICY_CONFIG_KEY``
(following the ``credit_costs`` singleton pattern) so the owner can tune
them without a code change:

- ``refund_credit_threshold``: monthly refunds are blocked when the user
  has consumed this many credits **in the current period**.  The usage is
  computed from the ledger (``CreditTransaction`` rows with
  ``created_at >= sub.period_start``) — never historical ``total_used``,
  which would lock out loyal users forever (plan.md §9.6).
- ``annual_cooling_days``: annual refunds are a hard policy — only
  requests within this many days of ``period_start`` are allowed.

Fase 2 adds the refund policy itself (``check_refund_eligibility`` + the
ledger-based ``compute_usage_in_period``); ``compute_prorated_due`` lands
in Fase 3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppConfig, CreditTransaction, Plan, UserSubscription

# Config key for the billing-policy singleton (upsert on this key).
BILLING_POLICY_CONFIG_KEY = "billing_policy"

DEFAULT_BILLING_POLICY: dict[str, Any] = {
    "refund_credit_threshold": 16,
    "annual_cooling_days": 14,
}


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


async def get_billing_policy(db: AsyncSession) -> dict[str, Any]:
    """Return the effective billing policy (validated over the defaults)."""
    result = await db.execute(
        select(AppConfig).where(AppConfig.key == BILLING_POLICY_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    stored = (row.value if row is not None else None) or {}
    policy = dict(DEFAULT_BILLING_POLICY)
    if isinstance(stored, dict):
        threshold = _non_negative_int(stored.get("refund_credit_threshold"))
        cooling = _non_negative_int(stored.get("annual_cooling_days"))
        if threshold is not None:
            policy["refund_credit_threshold"] = threshold
        if cooling is not None:
            policy["annual_cooling_days"] = cooling
    return policy


async def set_billing_policy(db: AsyncSession, policy: dict[str, Any]) -> dict[str, Any]:
    """Persist the billing policy (admin panel).

    Raises:
        ValueError: when a key is missing or not a non-negative integer.
    """
    if not isinstance(policy, dict):
        raise ValueError("billing_policy must be an object")
    threshold = _non_negative_int(policy.get("refund_credit_threshold"))
    cooling = _non_negative_int(policy.get("annual_cooling_days"))
    if threshold is None or cooling is None:
        raise ValueError(
            "billing_policy needs refund_credit_threshold >= 0 and annual_cooling_days >= 0"
        )
    cleaned = {
        "refund_credit_threshold": threshold,
        "annual_cooling_days": cooling,
    }
    result = await db.execute(
        select(AppConfig).where(AppConfig.key == BILLING_POLICY_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = AppConfig(key=BILLING_POLICY_CONFIG_KEY, value=cleaned)
        db.add(row)
    else:
        row.value = cleaned
    await db.flush()
    return await get_billing_policy(db)


# ── Refund policy (Fase 2) ───────────────────────────────────────────


def _utc(dt: datetime) -> datetime:
    """Normalize a DB datetime to timezone-aware UTC (SQLite is naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def billing_cycle_for(sub: UserSubscription) -> str:
    """Infer the billing cycle from the period span (like renew_subscription)."""
    if sub.period_start is not None and sub.period_end is not None:
        span_days = (sub.period_end - sub.period_start).days
        if span_days >= 360:
            return "yearly"
    return "monthly"


async def compute_usage_in_period(
    db: AsyncSession, sub: UserSubscription
) -> int:
    """Credits consumed **within the current billing period**, from the ledger.

    Sums the negative ``credits_delta`` rows of ``CreditTransaction`` with
    ``created_at >= sub.period_start``.  This is the honest measure for
    refund eligibility — ``account.total_used`` is lifetime and would lock
    out loyal users (plan.md §9.6).  Returns a non-negative integer.
    """
    if sub.period_start is None:
        return 0
    result = await db.execute(
        select(func.coalesce(func.sum(CreditTransaction.credits_delta), 0)).where(
            CreditTransaction.user_id == sub.user_id,
            CreditTransaction.credits_delta < 0,
            CreditTransaction.created_at >= sub.period_start,
        )
    )
    spent = int(result.scalar_one() or 0)
    return max(-spent, 0)


def check_refund_eligibility(
    sub: UserSubscription,
    usage_in_period: int,
    policy: dict[str, Any],
) -> tuple[bool, str | None]:
    """Apply the refund policy to a subscription.

    Returns ``(eligible, reason_code)`` where ``reason_code`` is one of
    ``"refund_usage_exceeded"`` / ``"refund_cooling_passed"`` when the
    refund is blocked, ``None`` when eligible.

    Rules (plan.md §2/§9.6):
    - monthly → blocked when ``usage_in_period >= refund_credit_threshold``;
    - yearly (hard policy) → blocked when more than ``annual_cooling_days``
      have passed since ``period_start``.
    """
    threshold = int(policy.get("refund_credit_threshold", 16))
    cooling_days = int(policy.get("annual_cooling_days", 14))

    if billing_cycle_for(sub) == "yearly":
        if sub.period_start is not None:
            elapsed = datetime.now(UTC) - _utc(sub.period_start)
            if elapsed > timedelta(days=cooling_days):
                return False, "refund_cooling_passed"
        return True, None

    if usage_in_period >= threshold:
        return False, "refund_usage_exceeded"
    return True, None


# ── Prorated upgrade (Fase 3) ────────────────────────────────────────


def compute_prorated_due(
    plan_from: Plan,
    plan_to: Plan,
    period_start: datetime | None,
    period_end: datetime | None,
) -> float:
    """Prorated amount due when upgrading mid-period (plan.md §2 Caso 5).

    ``amount_due = (days_left / days_total) * (price_to - price_from)`` — the
    user only pays for the portion of the target plan that replaces the
    remainder of their current period, minus the unused portion of the
    current plan.

    The billing cycle is inferred from the period span (monthly/yearly, same
    rule as ``renew_subscription``) and selects the matching price on each
    plan.  A negative due (equal or cheaper target — a downgrade) is clamped
    to 0: downgrades are not supported by this flow (plan.md §9.2); the
    caller rejects ``amount_due <= 0``.
    """
    if period_start is None or period_end is None:
        return float(plan_to.price_monthly_usd)

    start = _utc(period_start)
    end = _utc(period_end)
    total = (end - start).total_seconds()
    if total <= 0:
        return float(plan_to.price_monthly_usd)

    remaining = (end - datetime.now(UTC)).total_seconds()
    if remaining < 0:
        remaining = 0.0
    ratio = remaining / total

    cycle = "yearly" if (end - start).days >= 360 else "monthly"
    price_from = plan_from.price_yearly_usd if cycle == "yearly" else plan_from.price_monthly_usd
    price_to = plan_to.price_yearly_usd if cycle == "yearly" else plan_to.price_monthly_usd

    return max(round(ratio * (price_to - price_from), 2), 0.0)
