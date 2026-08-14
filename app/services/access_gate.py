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
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.db.models import Plan, User
from app.services import credits
from app.services.subscriptions import get_user_access


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
        HTTPException 429: quota exhausted.
        HTTPException 402: insufficient credits.
    """
    access = await get_user_access(db, user)
    if access["is_admin"]:
        return None

    plan: Plan | None = access["plan"]
    if plan is not None and (plan.daily_quota > 0 or plan.weekly_quota > 0):
        if not await credits.check_quota(db, user["sub"], plan):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Usage quota for this period is exhausted. "
                    "Try again later or raise the limits in the admin panel."
                ),
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
            detail=(
                "Not enough AI credits. Add credits or upgrade your plan. "
                f"Correlation ID: {correlation_id}"
            ),
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