"""Tests for the top-up flow (Fase 1).

Covers the three pieces of the manual top-up lifecycle:

- ``apply_topup`` (service): paid-plan gate + ``action="topup"`` ledger entry.
- ``POST /billing/topup`` (user): pack validation, 403 ``topup_requires_plan``
  for free users, ``topup_request`` notification for the admin.
- ``POST /admin/credits/topup`` (admin): approval applies the credits and
  auto-closes the pending notification; a plan that lapsed between request
  and approval is rejected with 409.

Same service-level style as ``test_access_gate.py``: in-memory SQLite +
seeded plan catalog, endpoints called directly with JWT-shaped user dicts.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.admin import approve_topup
from app.api.v1.billing import request_topup
from app.db.models import AppNotification, Base, CreditTransaction, User
from app.schemas.billing import AdminTopupApprove, TopupRequest
from app.services.plans import build_catalog
from tests.unit.plan_helpers import seed_test_plans
from app.services.subscriptions import activate_subscription
from app.services.topups import (
    DEFAULT_TOPUP_PACKS,
    TopupNotAllowedError,
    apply_topup,
    get_topup_packs,
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
async def free_user(db_session):
    u = User(id="free-1", email="free@example.com", hashed_password="x", role="client", tier="free")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def pro_user(db_session):
    u = User(id="pro-1", email="pro@example.com", hashed_password="x", role="client", tier="free")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


def _client_ctx(user_id: str) -> dict:
    return {"sub": user_id, "role": "client"}


def _admin_ctx() -> dict:
    return {"sub": "admin-1", "role": "admin"}


async def _notifications(db: AsyncSession, type_: str) -> list[AppNotification]:
    rows = await db.execute(
        select(AppNotification).where(AppNotification.type == type_)
    )
    return list(rows.scalars().all())


# ── Service: apply_topup ─────────────────────────────────────────────


async def test_apply_topup_requires_subscription(db_session, free_user):
    """No subscription at all → TopupNotAllowedError (not a silent grant)."""
    with pytest.raises(TopupNotAllowedError):
        await apply_topup(db_session, free_user.id, DEFAULT_TOPUP_PACKS[0])


async def test_apply_topup_rejects_free_plan(db_session, free_user):
    """Free plan subscription → TopupNotAllowedError (credits would wipe)."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()

    with pytest.raises(TopupNotAllowedError):
        await apply_topup(db_session, free_user.id, DEFAULT_TOPUP_PACKS[0])


async def test_apply_topup_rejects_inactive_plan(db_session, free_user):
    """A subscription on a plan that was disabled is not paid-eligible."""
    from app.services.plans import get_plan

    await activate_subscription(db_session, free_user, "pro", source="admin")
    await db_session.commit()
    plan = await get_plan(db_session, "pro")
    plan.is_active = False
    await db_session.commit()

    with pytest.raises(TopupNotAllowedError):
        await apply_topup(db_session, free_user.id, DEFAULT_TOPUP_PACKS[0])


async def test_apply_topup_rejects_invalid_pack(db_session, pro_user):
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    with pytest.raises(ValueError, match="Invalid top-up pack"):
        await apply_topup(db_session, pro_user.id, {"price_usd": 9.99, "credits": 0})


async def test_apply_topup_grants_credits_with_ledger_action(db_session, pro_user):
    """Paid plan → credits added on top of the period allowance, ledger
    row recorded with action='topup'."""
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    # Pro refills 100 credits on first check.
    from app.services.credits import get_balance

    assert (await get_balance(db_session, pro_user.id))["balance"] == 100

    account = await apply_topup(
        db_session, pro_user.id, DEFAULT_TOPUP_PACKS[0], correlation_id="topup-cid-1"
    )
    assert account.balance == 150  # 100 period allowance + 50 top-up

    txn = (await db_session.execute(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == pro_user.id)
        .order_by(CreditTransaction.created_at.desc())
    )).scalars().first()
    assert txn.action == "topup"
    assert txn.credits_delta == 50
    assert txn.correlation_id == "topup-cid-1"
    assert "50 credits" in (txn.description or "")


# ── User endpoint: POST /billing/topup ───────────────────────────────


async def test_request_topup_free_user_403(db_session, free_user, admin_user):
    """Free user → 403 with code='topup_requires_plan' (structured detail)."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await request_topup(
            TopupRequest(pack_credits=50),
            user=_client_ctx(free_user.id),
            db=db_session,
            locale="en",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "topup_requires_plan"


async def test_request_topup_no_subscription_403(db_session, free_user, admin_user):
    """No subscription at all → same 403 gate."""
    with pytest.raises(HTTPException) as exc:
        await request_topup(
            TopupRequest(pack_credits=50),
            user=_client_ctx(free_user.id),
            db=db_session,
            locale="en",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "topup_requires_plan"


async def test_request_topup_unknown_pack_422(db_session, pro_user, admin_user):
    """A pack outside the fixed catalog (e.g. 999 credits) → 422."""
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await request_topup(
            TopupRequest(pack_credits=999),
            user=_client_ctx(pro_user.id),
            db=db_session,
            locale="en",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "invalid_topup_pack"


async def test_request_topup_paid_user_creates_notification(db_session, pro_user, admin_user):
    """Paid user with a valid pack → TopupRequestOut + admin notification
    carrying {user_id, pack, credits, correlation_id}."""
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    out = await request_topup(
        TopupRequest(pack_credits=50, method="whatsapp"),
        user=_client_ctx(pro_user.id),
        db=db_session,
        locale="es",
    )
    assert out.ok is True
    assert out.correlation_id
    assert out.pack is not None and out.pack.credits == 50 and out.pack.price_usd == 9.99

    notifs = await _notifications(db_session, "topup_request")
    assert len(notifs) == 1
    payload = notifs[0].payload
    assert payload["user_id"] == pro_user.id
    assert payload["credits"] == 50
    assert payload["price_usd"] == 9.99
    assert payload["pack"] == {"price_usd": 9.99, "credits": 50}
    assert payload["correlation_id"] == out.correlation_id
    assert payload["method"] == "whatsapp"


async def test_request_topup_does_not_touch_balance(db_session, pro_user, admin_user):
    """The request endpoint only records intent — no credits are granted."""
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    from app.services.credits import get_balance

    before = (await get_balance(db_session, pro_user.id))["balance"]
    await request_topup(
        TopupRequest(pack_credits=120),
        user=_client_ctx(pro_user.id),
        db=db_session,
        locale="en",
    )
    after = (await get_balance(db_session, pro_user.id))["balance"]
    assert before == after == 100  # untouched


# ── Admin approval: POST /admin/credits/topup ────────────────────────


async def test_approve_topup_applies_and_closes_notification(db_session, pro_user, admin_user):
    """Admin approves → credits land, pending topup_request marked read."""
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    out = await request_topup(
        TopupRequest(pack_credits=50),
        user=_client_ctx(pro_user.id),
        db=db_session,
        locale="en",
    )
    assert len(await _notifications(db_session, "topup_request")) == 1

    resp = await approve_topup(
        AdminTopupApprove(
            user_id=pro_user.id,
            pack_credits=50,
            price_paid=9.99,
            correlation_id=out.correlation_id,
        ),
        admin=_admin_ctx(),
        db=db_session,
    )
    assert resp["credits"] == 50
    assert resp["balance"] == 150
    assert resp["price_paid"] == 9.99

    notifs = await _notifications(db_session, "topup_request")
    assert len(notifs) == 1 and notifs[0].is_read is True

    txn = (await db_session.execute(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == pro_user.id)
        .order_by(CreditTransaction.created_at.desc())
    )).scalars().first()
    assert txn.action == "topup"
    assert txn.correlation_id == out.correlation_id


async def test_approve_topup_rejects_lapsed_plan(db_session, free_user, admin_user):
    """Plan lapsed between request and approval → 409, nothing granted."""
    await activate_subscription(db_session, free_user, "pro", source="admin")
    await db_session.commit()

    # User downgrades back to free before the admin approves.
    await activate_subscription(db_session, free_user, "free", source="admin")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await approve_topup(
            AdminTopupApprove(user_id=free_user.id, pack_credits=50, price_paid=9.99),
            admin=_admin_ctx(),
            db=db_session,
        )
    assert exc.value.status_code == 409


async def test_approve_topup_unknown_pack_422(db_session, pro_user, admin_user):
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await approve_topup(
            AdminTopupApprove(user_id=pro_user.id, pack_credits=777, price_paid=9.99),
            admin=_admin_ctx(),
            db=db_session,
        )
    assert exc.value.status_code == 422


async def test_approve_topup_unknown_user_404(db_session, admin_user):
    with pytest.raises(HTTPException) as exc:
        await approve_topup(
            AdminTopupApprove(user_id="ghost", pack_credits=50, price_paid=9.99),
            admin=_admin_ctx(),
            db=db_session,
        )
    assert exc.value.status_code == 404


async def test_approve_topup_requires_price_paid(db_session, pro_user, admin_user):
    """plan.md §2.8: the admin must confirm the amount received — missing or
    non-positive price_paid is rejected at the schema level."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="price_paid"):
        AdminTopupApprove(user_id=pro_user.id, pack_credits=50)
    with pytest.raises(ValidationError, match="price_paid"):
        AdminTopupApprove(user_id=pro_user.id, pack_credits=50, price_paid=0)
    with pytest.raises(ValidationError, match="price_paid"):
        AdminTopupApprove(user_id=pro_user.id, pack_credits=50, price_paid=-5)


async def test_approve_topup_accepts_edited_amount(db_session, pro_user, admin_user):
    """The admin may confirm an amount different from the list price (e.g. a
    promo) as long as it is positive."""
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    resp = await approve_topup(
        AdminTopupApprove(user_id=pro_user.id, pack_credits=50, price_paid=7.5),
        admin=_admin_ctx(),
        db=db_session,
    )
    assert resp["credits"] == 50
    assert resp["price_paid"] == 7.5


# ── Catalog ──────────────────────────────────────────────────────────


async def test_catalog_includes_topup_packs(db_session):
    """build_catalog exposes the fixed packs for the buy/upgrade modal."""
    catalog = await build_catalog(db_session)
    assert catalog["topup_packs"] == await get_topup_packs(db_session)
    assert {p["credits"] for p in catalog["topup_packs"]} == {50, 120}
