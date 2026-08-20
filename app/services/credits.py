"""Credit service — balance, refills, quotas and the immutable ledger.

Every credit movement is a ``CreditTransaction`` row (never edited/deleted).
Actions that call the LLM (``cv_base``, ``cv_adapted``) consume credits from
the account balance; pipeline actions on ``max`` are gated by daily/weekly
quotas instead.  Refills follow the plan cadence:

- ``free``   → weekly refill (2 credits every 7 days, never accumulate)
- ``pro/max`` → refill at the start of each billing period
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CreditAccount, CreditTransaction, Plan, UserSubscription
from app.services.plans import get_credit_costs, get_plan

logger = logging.getLogger(__name__)


def _utc(dt: datetime | None) -> datetime:
    """Normalize a DB datetime to timezone-aware UTC (SQLite is naive)."""
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class NotEnoughCreditsError(Exception):
    """Raised when the user has fewer credits than the action requires."""

    def __init__(self, action: str, required: int, available: int, correlation_id: str):
        self.action = action
        self.required = required
        self.available = available
        self.correlation_id = correlation_id
        super().__init__(f"Not enough credits for '{action}': need {required}, have {available}.")


async def get_or_create_credit_account(
    db: AsyncSession, user_id: str, subscription: UserSubscription | None = None
) -> CreditAccount:
    """Return the user's credit account, creating it lazily if needed."""
    result = await db.execute(select(CreditAccount).where(CreditAccount.user_id == user_id))
    account = result.scalar_one_or_none()
    if account is None:
        # Anchor the last refill 7 days in the past so the very first check
        # triggers an immediate refill (new users get their welcome credits).
        account = CreditAccount(
            user_id=user_id,
            subscription_id=subscription.id if subscription else None,
            balance=0,
            last_refill_at=datetime.now(UTC) - timedelta(days=7),
            quota_day_reset_at=datetime.now(UTC),
            quota_week_reset_at=datetime.now(UTC),
        )
        db.add(account)
        await db.flush()
    return account


async def _record_transaction(
    db: AsyncSession,
    user_id: str,
    action: str,
    credits_delta: int,
    *,
    subscription: UserSubscription | None = None,
    correlation_id: str | None = None,
    description: str | None = None,
    model_used: str | None = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_usd_cents: int = 0,
) -> CreditTransaction:
    """Append an immutable ledger row."""
    txn = CreditTransaction(
        user_id=user_id,
        subscription_id=subscription.id if subscription else None,
        correlation_id=correlation_id or uuid.uuid4().hex,
        action=action,
        credits_delta=credits_delta,
        description=description,
        model_used=model_used,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd_cents=cost_usd_cents,
    )
    db.add(txn)
    await db.flush()
    return txn


async def refill_if_due(db: AsyncSession, user_id: str) -> CreditAccount:
    """Refill the user's credit balance when the cadence window has passed.

    - weekly cadence (free): refill every 7 days from the last refill.
    - period cadence (pro/max): refill when the subscription period turns.
    Credits never accumulate: the balance is *reset* to the plan allowance.
    """
    now = datetime.now(UTC)
    account = await get_or_create_credit_account(db, user_id)

    # Find the user's active subscription (if any).
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
    plan = None
    if subscription is not None:
        plan = await get_plan(db, subscription.plan_key)

    if plan is not None and plan.is_active and plan.refill_cadence == "weekly":
        # Weekly refill anchored on the last refill.  If there is no anchor
        # yet (fresh account), the refill is due immediately — this is the
        # welcome bonus for new users.
        if account.last_refill_at is not None:
            anchor = _utc(account.last_refill_at)
        elif account.created_at is not None:
            anchor = _utc(account.created_at)
        else:
            anchor = now - timedelta(days=7)
        if now >= anchor + timedelta(days=7):
            delta = plan.credits_per_period - account.balance
            if delta > 0:
                account.balance = plan.credits_per_period
                account.total_earned += delta
                account.last_refill_at = now
                await _record_transaction(
                    db,
                    user_id,
                    "refill",
                    delta,
                    subscription=subscription,
                    description=f"Weekly refill ({plan.key})",
                )
            elif delta < 0:
                # Non-accumulating: credits that were not used are reset.
                account.balance = plan.credits_per_period
                account.last_refill_at = now
                await _record_transaction(
                    db,
                    user_id,
                    "expiry",
                    delta,
                    subscription=subscription,
                    description=f"Unused credits expired at weekly refill ({plan.key})",
                )
            else:
                account.last_refill_at = now
    elif plan is not None and plan.is_active:
        # Period cadence: refill when a new period started after the last refill.
        period_due = subscription is not None and subscription.period_start is not None
        if period_due:
            period_start = _utc(subscription.period_start)
            if account.last_refill_at is None or period_start > _utc(account.last_refill_at):
                delta = plan.credits_per_period - account.balance
                if delta > 0:
                    account.balance = plan.credits_per_period
                    account.total_earned += delta
                    await _record_transaction(
                        db,
                        user_id,
                        "refill",
                        delta,
                        subscription=subscription,
                        description=f"Period refill ({plan.key})",
                    )
                else:
                    account.balance = plan.credits_per_period
                    await _record_transaction(
                        db,
                        user_id,
                        "expiry",
                        delta,
                        subscription=subscription,
                        description=f"Unused credits expired at period refill ({plan.key})",
                    )
                account.last_refill_at = now

    await db.flush()
    return account


async def check_credits(
    db: AsyncSession, user_id: str, action: str, required: int
) -> tuple[bool, CreditAccount | None, str | None]:
    """Return (can_run, account, correlation_id) without consuming.

    Runs the refill check first so freshly available credits count.
    """
    account = await refill_if_due(db, user_id)
    correlation_id = uuid.uuid4().hex
    return account.balance >= required, account, correlation_id


async def consume_credits(
    db: AsyncSession,
    user_id: str,
    action: str,
    required: int,
    *,
    correlation_id: str | None = None,
    description: str | None = None,
    model_used: str | None = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_usd_cents: int = 0,
) -> CreditAccount:
    """Consume ``required`` credits from the user's balance (atomic-ish).

    Raises:
        NotEnoughCreditsError: if the balance (after a due refill) is too low.
    """
    account = await refill_if_due(db, user_id)
    if account.balance < required:
        cid = correlation_id or uuid.uuid4().hex
        raise NotEnoughCreditsError(action, required, account.balance, cid)

    account.balance -= required
    account.total_used += required
    await _record_transaction(
        db,
        user_id,
        action,
        -required,
        correlation_id=correlation_id,
        description=description or f"{action}: consumed {required} credits",
        model_used=model_used,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd_cents=cost_usd_cents,
    )
    await db.flush()
    logger.info(
        "Credits consumed: user=%s action=%s cost=%s remaining=%s",
        user_id,
        action,
        required,
        account.balance,
    )
    return account


async def adjust_credits(
    db: AsyncSession,
    user_id: str,
    delta: int,
    action: str = "admin_adjust",
    *,
    correlation_id: str | None = None,
    description: str | None = None,
) -> CreditAccount:
    """Add (delta > 0) or remove (delta < 0) credits manually (admin)."""
    account = await refill_if_due(db, user_id)
    new_balance = account.balance + delta
    if new_balance < 0:
        new_balance = 0
    applied = new_balance - account.balance
    if applied > 0:
        account.total_earned += applied
    elif applied < 0:
        account.total_used += -applied
    account.balance = new_balance
    await _record_transaction(
        db,
        user_id,
        action,
        applied,
        correlation_id=correlation_id,
        description=description,
    )
    await db.flush()
    return account


async def get_balance(db: AsyncSession, user_id: str) -> dict:
    """Return the user's credit balance + quota usage (refills applied)."""
    account = await refill_if_due(db, user_id)

    # Quota reset bookkeeping.
    now = datetime.now(UTC)
    day_due = account.quota_day_reset_at is None
    day_due = day_due or now >= _utc(account.quota_day_reset_at) + timedelta(days=1)
    week_due = account.quota_week_reset_at is None
    week_due = week_due or now >= _utc(account.quota_week_reset_at) + timedelta(days=7)
    if day_due:
        account.quota_day_used = 0
        account.quota_day_reset_at = now
    if week_due:
        account.quota_week_used = 0
        account.quota_week_reset_at = now
    await db.flush()

    return {
        "balance": account.balance,
        "total_earned": account.total_earned,
        "total_used": account.total_used,
        "quota_day_used": account.quota_day_used,
        "quota_week_used": account.quota_week_used,
        # When the current quota windows reset (start of window + span).
        "next_quota_reset_at": next_quota_reset_at(account),
    }


def next_quota_reset_at(account: CreditAccount) -> datetime | None:
    """Earliest upcoming quota-window reset for the account.

    ``quota_day_reset_at`` / ``quota_week_reset_at`` mark the *start* of the
    current window, so the next reset is start + 1 day / + 7 days.  Only
    windows that have actually started count.  ``None`` when the account has
    no quota windows at all.

    Single source of truth shared by the billing status endpoint (weekly
    quota bar) and the 429 gate detail (plan.md §4).
    """
    now = datetime.now(UTC)
    candidates: list[datetime] = []
    if account.quota_day_reset_at is not None:
        candidates.append(_utc(account.quota_day_reset_at) + timedelta(days=1))
    if account.quota_week_reset_at is not None:
        candidates.append(_utc(account.quota_week_reset_at) + timedelta(days=7))
    future = [c for c in candidates if c > now]
    if future:
        return min(future)
    return min(candidates) if candidates else None


async def check_quota(db: AsyncSession, user_id: str, plan: Plan, count: int = 1) -> bool:
    """Return True if ``count`` more quota units fit within the plan quotas."""
    account = await get_or_create_credit_account(db, user_id)
    now = datetime.now(UTC)
    day_due = account.quota_day_reset_at is None
    day_due = day_due or now >= _utc(account.quota_day_reset_at) + timedelta(days=1)
    week_due = account.quota_week_reset_at is None
    week_due = week_due or now >= _utc(account.quota_week_reset_at) + timedelta(days=7)
    if day_due:
        account.quota_day_used = 0
        account.quota_day_reset_at = now
    if week_due:
        account.quota_week_used = 0
        account.quota_week_reset_at = now
    await db.flush()

    if plan.daily_quota > 0 and account.quota_day_used + count > plan.daily_quota:
        return False
    return not (plan.weekly_quota > 0 and account.quota_week_used + count > plan.weekly_quota)


async def consume_quota(db: AsyncSession, user_id: str, plan: Plan, count: int = 1) -> None:
    """Record quota usage (call only after :func:`check_quota` passed)."""
    account = await get_or_create_credit_account(db, user_id)
    now = datetime.now(UTC)
    day_due = account.quota_day_reset_at is None
    day_due = day_due or now >= _utc(account.quota_day_reset_at) + timedelta(days=1)
    week_due = account.quota_week_reset_at is None
    week_due = week_due or now >= _utc(account.quota_week_reset_at) + timedelta(days=7)
    if day_due:
        account.quota_day_used = 0
        account.quota_day_reset_at = now
    if week_due:
        account.quota_week_used = 0
        account.quota_week_reset_at = now
    account.quota_day_used += count
    account.quota_week_used += count
    await db.flush()


async def record_llm_usage(
    db: AsyncSession,
    correlation_id: str,
    *,
    model_used: str | None = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_usd_cents: int = 0,
) -> CreditTransaction | None:
    """Accumulate real LLM usage onto the ledger row for ``correlation_id``.

    Ledger rows are immutable in their delta but the token/cost columns are
    enriched after the fact: the credit is consumed before the LLM call runs
    and the actual usage is known only afterwards. Multiple sub-calls within
    one request share the same correlation_id and accumulate on one row.

    Best-effort: silently no-ops when no row matches (e.g. admin bypass).
    """
    if not correlation_id:
        return None
    result = await db.execute(select(CreditTransaction).where(CreditTransaction.correlation_id == correlation_id))
    txn = result.scalars().first()
    if txn is None:
        return None
    if model_used:
        txn.model_used = model_used
    if tokens_input:
        txn.tokens_input = (txn.tokens_input or 0) + int(tokens_input)
    if tokens_output:
        txn.tokens_output = (txn.tokens_output or 0) + int(tokens_output)
    if cost_usd_cents:
        txn.cost_usd_cents = (txn.cost_usd_cents or 0) + int(cost_usd_cents)
    await db.flush()
    return txn


async def relink_transaction(db: AsyncSession, correlation_id: str, new_correlation_id: str) -> bool:
    """Point a ledger row at a correlation id only known after consumption.

    Used by the rank flow: the credit is consumed before the ExecutionJob
    exists, then the row is relinked to the job id so the worker's usage can
    be matched back later.
    """
    if not correlation_id or not new_correlation_id:
        return False
    result = await db.execute(select(CreditTransaction).where(CreditTransaction.correlation_id == correlation_id))
    txn = result.scalars().first()
    if txn is None:
        return False
    txn.correlation_id = new_correlation_id
    await db.flush()
    return True


async def get_recent_transactions(db: AsyncSession, user_id: str, limit: int = 30) -> list[CreditTransaction]:
    """Most recent ledger rows for the user."""
    result = await db.execute(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_action_cost(db: AsyncSession, action: str) -> int:
    """Return the credit cost for an action from the admin config."""
    costs = await get_credit_costs(db)
    return costs.get(action, 0)
