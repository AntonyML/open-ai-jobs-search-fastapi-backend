"""Unified per-action access gate (credits + quotas).

Every LLM workflow (cv, rank, expand, upskill, apply, interview) must go
through :func:`enforce_action_gate` before starting so that:
  - quotas (daily/weekly) act as a circuit breaker on quota-bearing plans,
  - the action's credit cost is charged to the balance on every non-admin call,
  - usage accounting gets a correlation_id the underlying LLM calls can reuse.

Policy (confirmed with the owner):
  - admin → bypass entirely (returns ``None``, nothing consumed).
  - quota-bearing plans (e.g. max) → quotas gate the call *and* the credit
    cost is charged.
  - credit-only plans (free/pro) → only the credit cost is charged.

The 402/429 errors carry a structured ``detail`` dict (plan.md §4) so the
frontend can key on ``code`` and render the right CTA: ``insufficient_credits``
→ top-up modal (paid plans) / upgrade (free), ``quota_exceeded`` → weekly
quota bar (quotas are never monetizable).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status

from app.db.models import Plan, User
from app.services import credits
from app.services.credit_costs import CATALOG_BY_KEY
from app.services.subscriptions import get_user_access
from app.services.topups import get_topup_packs


def _utc(dt: datetime) -> datetime:
    """Normalize a DB datetime to timezone-aware UTC (SQLite is naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _next_refill_at(subscription: Any, plan: Plan | None, account: Any) -> datetime | None:
    """When the user can expect fresh credits.

    - paid plans (period cadence) → ``period_end`` (credits refill per period);
    - free (weekly cadence) → ``last_refill_at + 7 days``.
    """
    if plan is not None and plan.refill_cadence == "weekly" and account.last_refill_at is not None:
        return _utc(account.last_refill_at) + timedelta(days=7)
    if subscription is not None and subscription.period_end is not None:
        return _utc(subscription.period_end)
    return None


def _iso(dt: datetime | None) -> str | None:
    """ISO string for the JSON detail (FastAPI cannot serialize datetimes)."""
    return dt.isoformat() if dt is not None else None


async def enforce_action_gate(
    db: Any,
    user: dict[str, Any] | User,
    action: str,
    *,
    label: str | None = None,
) -> str | None:
    """Gate an LLM workflow, consuming quota + credits and returning a correlation_id.

    Returns:
        The correlation_id to attach to ``record_llm_usage`` for this request,
        or ``None`` when no ledger row was created (admin bypass / zero cost).

    Raises:
        HTTPException 429 (detail ``{code: "quota_exceeded", ...}``): quota exhausted.
        HTTPException 402 (detail ``{code: "insufficient_credits", ...}``): insufficient credits.
    """
    access = await get_user_access(db, user)
    if access["is_admin"]:
        return None

    # Feature gate (plan.md §8.2 F4): a catalog action whose plan feature is
    # missing → 403 before any quota/credit is touched.  ``feature_gate=None``
    # (e.g. ``verify``) is available to every plan.
    spec = CATALOG_BY_KEY.get(action)
    if spec is not None and spec.feature_gate is not None:
        if spec.feature_gate not in access["features"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "feature_required",
                    "message": (
                        f"Your current plan does not include this action. "
                        f"Upgrade to unlock it."
                    ),
                    "feature": spec.feature_gate,
                    "plan_key": access["plan_key"],
                },
            )

    plan: Plan | None = access["plan"]
    if plan is not None and (plan.daily_quota > 0 or plan.weekly_quota > 0):
        if not await credits.check_quota(db, user["sub"], plan):
            # Refill first so the detail shows the real balance (the period
            # allowance is granted regardless of the quota state).
            account = await credits.refill_if_due(db, user["sub"])
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "quota_exceeded",
                    "message": (
                        "Usage quota for this period is exhausted. "
                        "Try again later or raise the limits in the admin panel."
                    ),
                    "balance": account.balance,
                    "next_reset_at": _iso(credits.next_quota_reset_at(account)),
                    "quota_week_used": account.quota_week_used,
                    "quota_week_limit": access["weekly_quota"],
                    "topup_packs": await get_topup_packs(db),
                },
            )
        await credits.consume_quota(db, user["sub"], plan)

    required = await credits.get_action_cost(db, action)
    if required <= 0:
        return None

    can_run, account, correlation_id = await credits.check_credits(
        db, user["sub"], action, required
    )
    if not can_run:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "insufficient_credits",
                "message": (
                    "Not enough AI credits. Add credits or upgrade your plan. "
                    f"Correlation ID: {correlation_id}"
                ),
                "balance": account.balance,
                "next_reset_at": _iso(
                    _next_refill_at(access["subscription"], plan, account)
                ),
                "quota_week_used": account.quota_week_used,
                "quota_week_limit": access["weekly_quota"],
                "topup_packs": await get_topup_packs(db),
                "correlation_id": correlation_id,
            },
        )
    await credits.consume_credits(
        db,
        user["sub"],
        action,
        required,
        correlation_id=correlation_id,
        description=f"{action}: {label or 'AI generation'}",
    )
    return correlation_id
