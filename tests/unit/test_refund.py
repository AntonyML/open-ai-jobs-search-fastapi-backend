"""Tests for cancel / refund (Fase 2).

Covers the policy layer (ledger-based usage-in-period + eligibility rules),
the subscription lifecycle (cancel keeps paid days, refund zeroes out) and
the three endpoints: ``POST /billing/cancel``, ``POST /billing/refund``
(403s at origin) and ``POST /admin/credits/refund`` (zero-out + refunded).

Same service-level style as the other billing tests: in-memory SQLite +
seeded plan catalog, endpoints called directly with JWT-shaped user dicts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.admin import approve_refund
from app.api.v1.billing import cancel_my_subscription, request_refund
from app.db.models import AppNotification, Base, CreditTransaction, User, UserSubscription
from app.schemas.billing import AdminRefundApprove
from app.services import credits
from app.services.billing_policy import (
    check_refund_eligibility,
    compute_usage_in_period,
    get_billing_policy,
)
from tests.unit.plan_helpers import seed_test_plans
from app.services.subscriptions import (
    activate_subscription,
    cancel_subscription,
    get_active_subscription,
    process_expired_subscriptions,
    refund_subscription,
)


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        await seed_test_plans(session)
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
async def admin_user(db_session):
    u = User(
        id="admin-1", email="admin@example.com", hashed_password="x",
        role="admin", tier="free",
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def user(db_session):
    u = User(id="user-1", email="user@example.com", hashed_password="x", role="client", tier="free")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


def _client_ctx(user_id: str) -> dict:
    return {"sub": user_id, "role": "client"}


def _admin_ctx() -> dict:
    return {"sub": "admin-1", "role": "admin"}


async def _notifications(db: AsyncSession, type_: str) -> list[AppNotification]:
    rows = await db.execute(select(AppNotification).where(AppNotification.type == type_))
    return list(rows.scalars().all())


async def _ledger_rows(db: AsyncSession, action: str) -> list[CreditTransaction]:
    rows = await db.execute(
        select(CreditTransaction).where(CreditTransaction.action == action)
    )
    return list(rows.scalars().all())


async def _make_sub(
    db: AsyncSession,
    user_id: str,
    plan_key: str = "pro",
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    status: str = "active",
    auto_renew: bool = False,
) -> UserSubscription:
    now = datetime.now(UTC)
    sub = UserSubscription(
        user_id=user_id,
        plan_key=plan_key,
        correlation_id=uuid.uuid4().hex,
        period_start=period_start or now,
        period_end=period_end or (now + timedelta(days=30)),
        status=status,
        source="purchase",
        auto_renew=auto_renew,
    )
    db.add(sub)
    await db.flush()
    return sub


# ── Policy: usage in period (ledger) ────────────────────────────────


async def test_compute_usage_in_period_sums_only_negative_within_period(db_session, user):
    """Only negative deltas since period_start count; refills and older rows don't."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=5)
    sub = await _make_sub(db_session, user.id, period_start=period_start)
    await db_session.commit()

    # Older consumption (before this period) — must not count.
    db_session.add(
        CreditTransaction(
            user_id=user.id,
            correlation_id=uuid.uuid4().hex,
            action="cv_base",
            credits_delta=-3,
            description="older usage",
            created_at=period_start - timedelta(days=1),
        )
    )
    # Current-period consumption — counts.
    await credits.adjust_credits(db_session, user.id, -10, action="cv_base")
    await credits.adjust_credits(db_session, user.id, -6, action="cv_adapted")

    assert await compute_usage_in_period(db_session, sub) == 16


async def test_compute_usage_in_period_zero_without_period_start(db_session, user):
    # No period bounds → usage is 0 even when negative ledger rows exist.
    sub = UserSubscription(
        user_id=user.id,
        plan_key="pro",
        correlation_id=uuid.uuid4().hex,
        period_start=None,
        period_end=None,
        status="active",
        source="purchase",
    )
    db_session.add(sub)
    await db_session.flush()
    db_session.add(
        CreditTransaction(
            user_id=user.id,
            correlation_id=uuid.uuid4().hex,
            action="cv_base",
            credits_delta=-5,
        )
    )
    await db_session.flush()

    assert await compute_usage_in_period(db_session, sub) == 0


# ── Policy: eligibility rules ────────────────────────────────────────


async def test_check_refund_eligibility_monthly_under_threshold(db_session, user):
    policy = await get_billing_policy(db_session)
    sub = await _make_sub(db_session, user.id)  # 30-day span → monthly
    eligible, reason = check_refund_eligibility(sub, usage_in_period=15, policy=policy)
    assert eligible is True and reason is None


async def test_check_refund_eligibility_monthly_over_threshold(db_session, user):
    policy = await get_billing_policy(db_session)
    sub = await _make_sub(db_session, user.id)
    eligible, reason = check_refund_eligibility(sub, usage_in_period=16, policy=policy)
    assert eligible is False and reason == "refund_usage_exceeded"


async def test_check_refund_eligibility_yearly_within_cooling(db_session, user):
    policy = await get_billing_policy(db_session)
    now = datetime.now(UTC)
    sub = await _make_sub(
        db_session, user.id,
        period_start=now - timedelta(days=5),
        period_end=now + timedelta(days=360),
    )
    eligible, reason = check_refund_eligibility(sub, usage_in_period=999, policy=policy)
    assert eligible is True and reason is None


async def test_check_refund_eligibility_yearly_after_cooling(db_session, user):
    policy = await get_billing_policy(db_session)
    now = datetime.now(UTC)
    sub = await _make_sub(
        db_session, user.id,
        period_start=now - timedelta(days=20),
        period_end=now + timedelta(days=345),
    )
    eligible, reason = check_refund_eligibility(sub, usage_in_period=0, policy=policy)
    assert eligible is False and reason == "refund_cooling_passed"


# ── Cancel ───────────────────────────────────────────────────────────


async def test_cancel_subscription_keeps_active_until_period_end(db_session, user):
    sub = await activate_subscription(db_session, user, "pro", source="admin", auto_renew=True)
    await db_session.commit()

    await cancel_subscription(db_session, sub)
    await db_session.flush()

    assert sub.status == "active"  # paid days intact
    assert sub.auto_renew is False
    assert sub.cancelled_at is not None

    # Idempotent: a second cancel does not change anything.
    before = sub.cancelled_at
    await cancel_subscription(db_session, sub)
    assert sub.cancelled_at == before


async def test_cancel_then_expiry_drops_tier_to_free(db_session, user):
    """A cancelled sub still expires normally: process_expired_subscriptions
    flips it to expired and resets the tier (plan.md §2 Caso 4)."""
    now = datetime.now(UTC)
    sub = await _make_sub(
        db_session, user.id,
        period_start=now - timedelta(days=40),
        period_end=now - timedelta(days=10),
        auto_renew=False,
    )
    user.tier = "pro"
    await db_session.commit()

    await cancel_subscription(db_session, sub)  # stamp cancelled_at
    changed = await process_expired_subscriptions(db_session)
    assert changed == 1
    assert sub.status == "expired"
    assert user.tier == "free"


async def test_activate_subscription_supersedes_with_cancelled_at(db_session, user):
    """Superseded subs are stamped cancelled + cancelled_at (plan.md §9.3)."""
    old = await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()

    await activate_subscription(db_session, user, "max", source="admin")
    await db_session.flush()

    assert old.status == "cancelled"
    assert old.cancelled_at is not None
    assert (await get_active_subscription(db_session, user.id)).plan_key == "max"


async def test_cancel_endpoint(db_session, user):
    await activate_subscription(db_session, user, "pro", source="admin", auto_renew=True)
    await db_session.commit()

    out = await cancel_my_subscription(user=_client_ctx(user.id), db=db_session, locale="es")
    assert out.ok is True
    assert out.period_end is not None
    sub = await get_active_subscription(db_session, user.id)
    assert sub is not None
    assert sub.auto_renew is False and sub.cancelled_at is not None


async def test_cancel_endpoint_without_subscription_404(db_session, user):
    with pytest.raises(HTTPException) as exc:
        await cancel_my_subscription(user=_client_ctx(user.id), db=db_session, locale="en")
    assert exc.value.status_code == 404


# ── Refund: zero-out ─────────────────────────────────────────────────


async def test_refund_subscription_zeroes_balance(db_session, user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()
    assert (await credits.get_balance(db_session, user.id))["balance"] == 100

    sub = await get_active_subscription(db_session, user.id)
    _sub, revoked = await refund_subscription(db_session, sub)

    assert revoked == 100
    assert sub.status == "refunded"
    assert sub.cancelled_at is not None
    assert user.tier == "free"
    assert (await credits.get_balance(db_session, user.id))["balance"] == 0

    rows = await _ledger_rows(db_session, "refund_revoke")
    assert len(rows) == 1
    assert rows[0].credits_delta == -100


async def test_refund_subscription_skips_delta_when_balance_zero(db_session, user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()
    # No refill happened → balance is 0.

    sub = await get_active_subscription(db_session, user.id)
    _sub, revoked = await refund_subscription(db_session, sub)

    assert revoked == 0
    assert sub.status == "refunded"
    assert await _ledger_rows(db_session, "refund_revoke") == []


async def test_refund_subscription_is_idempotent(db_session, user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()
    assert (await credits.get_balance(db_session, user.id))["balance"] == 100

    sub = await get_active_subscription(db_session, user.id)
    _sub, revoked = await refund_subscription(db_session, sub)
    assert revoked == 100
    assert len(await _ledger_rows(db_session, "refund_revoke")) == 1

    # Second approval must not re-run the zero-out (plan.md §3.5).
    sub2 = await get_active_subscription(db_session, user.id)
    assert sub2 is None or sub2.status != "active"
    _sub, revoked2 = await refund_subscription(db_session, sub)
    assert revoked2 == 0
    assert sub.status == "refunded"
    assert len(await _ledger_rows(db_session, "refund_revoke")) == 1


# ── Endpoint: POST /billing/refund ──────────────────────────────────


async def test_request_refund_403_monthly_usage_exceeded(db_session, user, admin_user):
    """Monthly user who consumed >= 16 credits this period → 403 at origin;
    no refund_request notification is ever created."""
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()
    await credits.consume_credits(db_session, user.id, "cv_base", 16)

    with pytest.raises(HTTPException) as exc:
        await request_refund(user=_client_ctx(user.id), db=db_session, locale="en")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "refund_usage_exceeded"
    assert await _notifications(db_session, "refund_request") == []


async def test_request_refund_403_yearly_cooling_passed(db_session, user, admin_user):
    now = datetime.now(UTC)
    await _make_sub(
        db_session, user.id,
        period_start=now - timedelta(days=20),
        period_end=now + timedelta(days=345),
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await request_refund(user=_client_ctx(user.id), db=db_session, locale="en")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "refund_cooling_passed"
    assert await _notifications(db_session, "refund_request") == []


async def test_request_refund_404_without_subscription(db_session, user):
    with pytest.raises(HTTPException) as exc:
        await request_refund(user=_client_ctx(user.id), db=db_session, locale="en")
    assert exc.value.status_code == 404


async def test_request_refund_eligible_creates_notification(db_session, user, admin_user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()

    out = await request_refund(user=_client_ctx(user.id), db=db_session, locale="es")
    assert out.ok is True
    assert out.correlation_id

    notifs = await _notifications(db_session, "refund_request")
    assert len(notifs) == 1
    payload = notifs[0].payload
    assert payload["user_id"] == user.id
    assert payload["plan_key"] == "pro"
    assert payload["correlation_id"] == out.correlation_id
    assert payload["usage_in_period"] == 0


# ── Endpoint: POST /admin/credits/refund ────────────────────────────


async def test_approve_refund_zeroes_and_marks_read(db_session, user, admin_user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()
    assert (await credits.get_balance(db_session, user.id))["balance"] == 100

    out = await request_refund(user=_client_ctx(user.id), db=db_session, locale="en")
    assert len(await _notifications(db_session, "refund_request")) == 1

    resp = await approve_refund(
        AdminRefundApprove(user_id=user.id, correlation_id=out.correlation_id),
        admin=_admin_ctx(),
        db=db_session,
    )
    assert resp["revoked_credits"] == 100
    assert resp["status"] == "refunded"

    notifs = await _notifications(db_session, "refund_request")
    assert len(notifs) == 1 and notifs[0].is_read is True
    assert (await credits.get_balance(db_session, user.id))["balance"] == 0
    assert user.tier == "free"


async def test_approve_refund_idempotent(db_session, user, admin_user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()
    assert (await credits.get_balance(db_session, user.id))["balance"] == 100

    sub = await get_active_subscription(db_session, user.id)
    await refund_subscription(db_session, sub)
    await db_session.flush()

    # Approval after the refund is already executed → no double zero-out.
    resp = await approve_refund(
        AdminRefundApprove(user_id=user.id),
        admin=_admin_ctx(),
        db=db_session,
    )
    assert resp["revoked_credits"] == 0
    assert resp["status"] == "refunded"
    assert len(await _ledger_rows(db_session, "refund_revoke")) == 1


async def test_approve_refund_no_active_subscription_404(db_session, user, admin_user):
    with pytest.raises(HTTPException) as exc:
        await approve_refund(
            AdminRefundApprove(user_id=user.id),
            admin=_admin_ctx(),
            db=db_session,
        )
    assert exc.value.status_code == 404


async def test_approve_refund_unknown_user_404(db_session, admin_user):
    with pytest.raises(HTTPException) as exc:
        await approve_refund(
            AdminRefundApprove(user_id="ghost"),
            admin=_admin_ctx(),
            db=db_session,
        )
    assert exc.value.status_code == 404
