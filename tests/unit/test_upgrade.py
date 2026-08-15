"""Tests for the prorated upgrade (Fase 3).

Covers ``compute_prorated_due`` (cycle-aware proration, clamp to 0), the
``POST /billing/upgrade`` endpoint (404/422 guards + ``upgrade_prorate``
notification with ``amount_due``) and the admin side: ``price_paid`` is
forwarded to ``activate_subscription`` and pending upgrade notifications
are auto-closed (plan.md §2 Caso 5 / §5 / §9.2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.admin import create_subscription
from app.api.v1.billing import request_upgrade
from app.db.models import AppNotification, Base, User
from app.schemas.billing import AdminSubscriptionCreate, UpgradeRequest
from app.services.billing_policy import compute_prorated_due
from app.services.plans import get_plan, seed_default_plans
from app.services.subscriptions import activate_subscription


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        await seed_default_plans(session)
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


# ── compute_prorated_due ─────────────────────────────────────────────


async def test_prorated_due_mid_month(db_session):
    """pro → max halfway through a monthly period: (15/30) × $40 = $20."""
    pro = await get_plan(db_session, "pro")
    max_ = await get_plan(db_session, "max")
    now = datetime.now(UTC)
    due = compute_prorated_due(
        pro, max_,
        period_start=now - timedelta(days=15),
        period_end=now + timedelta(days=15),
    )
    assert due == pytest.approx(20.0, abs=0.01)


async def test_prorated_due_same_plan_is_zero(db_session):
    pro = await get_plan(db_session, "pro")
    now = datetime.now(UTC)
    due = compute_prorated_due(
        pro, pro,
        period_start=now - timedelta(days=15),
        period_end=now + timedelta(days=15),
    )
    assert due == 0.0


async def test_prorated_due_downgrade_clamped_to_zero(db_session):
    """max → pro is negative → clamped to 0 (plan.md §9.2 safety net)."""
    pro = await get_plan(db_session, "pro")
    max_ = await get_plan(db_session, "max")
    now = datetime.now(UTC)
    due = compute_prorated_due(
        max_, pro,
        period_start=now - timedelta(days=15),
        period_end=now + timedelta(days=15),
    )
    assert due == 0.0


async def test_prorated_due_yearly_uses_yearly_prices(db_session):
    pro = await get_plan(db_session, "pro")
    max_ = await get_plan(db_session, "max")
    now = datetime.now(UTC)
    due = compute_prorated_due(
        pro, max_,
        period_start=now - timedelta(days=180),
        period_end=now + timedelta(days=185),
    )
    # yearly: (185/365) × (599 − 199) ≈ 202.74 — never the monthly math.
    assert due == pytest.approx((185 / 365) * 400, abs=0.5)
    assert 0 < due < 400


async def test_prorated_due_expired_period_is_zero(db_session):
    pro = await get_plan(db_session, "pro")
    max_ = await get_plan(db_session, "max")
    now = datetime.now(UTC)
    due = compute_prorated_due(
        pro, max_,
        period_start=now - timedelta(days=31),
        period_end=now - timedelta(days=1),
    )
    assert due == 0.0


async def test_prorated_due_without_period_bounds(db_session):
    """No period bounds → fall back to the target monthly price."""
    pro = await get_plan(db_session, "pro")
    max_ = await get_plan(db_session, "max")
    assert compute_prorated_due(pro, max_, None, None) == max_.price_monthly_usd


# ── Endpoint: POST /billing/upgrade ─────────────────────────────────


async def test_request_upgrade_404_without_subscription(db_session, user):
    with pytest.raises(HTTPException) as exc:
        await request_upgrade(
            UpgradeRequest(plan_key="max"),
            user=_client_ctx(user.id),
            db=db_session,
            locale="en",
        )
    assert exc.value.status_code == 404


async def test_request_upgrade_404_unknown_target(db_session, user, admin_user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await request_upgrade(
            UpgradeRequest(plan_key="does-not-exist"),
            user=_client_ctx(user.id),
            db=db_session,
            locale="en",
        )
    assert exc.value.status_code == 404


async def test_request_upgrade_422_same_plan(db_session, user, admin_user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await request_upgrade(
            UpgradeRequest(plan_key="pro"),
            user=_client_ctx(user.id),
            db=db_session,
            locale="en",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "not_an_upgrade"


async def test_request_upgrade_422_downgrade(db_session, user, admin_user):
    """Downgrades are not supported by this endpoint (plan.md §9.2)."""
    await activate_subscription(db_session, user, "max", source="purchase")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await request_upgrade(
            UpgradeRequest(plan_key="pro"),
            user=_client_ctx(user.id),
            db=db_session,
            locale="en",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "not_an_upgrade"


async def test_request_upgrade_valid_creates_notification(db_session, user, admin_user):
    """pro → max mid-period: amount_due > 0 and the admin gets an
    upgrade_prorate notification carrying it."""
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()

    out = await request_upgrade(
        UpgradeRequest(plan_key="max", method="whatsapp"),
        user=_client_ctx(user.id),
        db=db_session,
        locale="es",
    )
    assert out.ok is True
    assert out.amount_due > 0
    assert out.correlation_id

    notifs = await _notifications(db_session, "upgrade_prorate")
    assert len(notifs) == 1
    payload = notifs[0].payload
    assert payload["user_id"] == user.id
    assert payload["plan_from"] == "pro"
    assert payload["plan_to"] == "max"
    assert payload["amount_due"] == out.amount_due
    assert payload["correlation_id"] == out.correlation_id
    assert payload["method"] == "whatsapp"
    # The upgrade keeps the user's current billing cycle (plan.md §9.2) and
    # the notification carries it so the admin activates with the same cycle.
    assert payload["billing_cycle"] == "monthly"


async def test_upgrade_payload_infers_yearly_cycle(db_session, user, admin_user):
    """A subscription on a ~1-year period reports billing_cycle=yearly."""
    sub = await activate_subscription(db_session, user, "pro", source="purchase")
    sub.period_start = datetime.now(UTC) - timedelta(days=30)
    sub.period_end = datetime.now(UTC) + timedelta(days=335)
    await db_session.commit()

    out = await request_upgrade(
        UpgradeRequest(plan_key="max", method="email"),
        user=_client_ctx(user.id),
        db=db_session,
        locale="es",
    )
    assert out.ok is True
    payload = (await _notifications(db_session, "upgrade_prorate"))[0].payload
    assert payload["billing_cycle"] == "yearly"


# ── Admin: price_paid + auto-close ──────────────────────────────────


async def test_create_subscription_forwards_price_paid(db_session, user, admin_user):
    out = await create_subscription(
        AdminSubscriptionCreate(user_id=user.id, plan_key="max", price_paid=20.0),
        admin=_admin_ctx(),
        db=db_session,
    )
    assert out.price_paid == 20.0


async def test_create_subscription_marks_upgrade_requests_read(db_session, user, admin_user):
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()

    out = await request_upgrade(
        UpgradeRequest(plan_key="max"),
        user=_client_ctx(user.id),
        db=db_session,
        locale="en",
    )
    assert len(await _notifications(db_session, "upgrade_prorate")) == 1

    await create_subscription(
        AdminSubscriptionCreate(
            user_id=user.id, plan_key="max", price_paid=out.amount_due
        ),
        admin=_admin_ctx(),
        db=db_session,
    )

    notifs = await _notifications(db_session, "upgrade_prorate")
    assert len(notifs) == 1 and notifs[0].is_read is True
