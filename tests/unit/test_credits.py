"""Tests for the credits/subscriptions services (billing phase 1).

Uses an in-memory SQLite database.  The services do not touch the network,
so no mocks are required beyond the ORM.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, User
from app.services import credits
from app.services.credits import NotEnoughCreditsError
from app.services.plans import get_active_plans, get_credit_costs, get_plan, seed_default_plans
from app.services.subscriptions import (
    activate_subscription,
    can_use_feature,
    ensure_admin_subscription,
    expire_subscription,
    get_user_access,
    process_expired_subscriptions,
)


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
async def user(db_session):
    u = User(id="user-1", email="u1@example.com", hashed_password="x", role="client", tier="free")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _admin(db_session):
    a = User(
        id="admin-1",
        email="admin@example.com",
        hashed_password="x",
        role="admin",
        tier="free",
    )
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


# ── Plans catalog ────────────────────────────────────────────────────


async def test_seed_default_plans(db_session):
    plans = await get_active_plans(db_session)
    keys = {p.key for p in plans}
    assert {"free", "pro", "max"} <= keys
    free = await get_plan(db_session, "free")
    assert free is not None
    assert free.credits_per_period == 2
    assert free.refill_cadence == "weekly"
    assert free.has_feature("cv_base")
    assert not free.has_feature("pipeline")
    max_plan = await get_plan(db_session, "max")
    assert max_plan.has_feature("pipeline")


async def test_credit_costs_defaults(db_session):
    costs = await get_credit_costs(db_session)
    assert costs["cv_base"] >= 1
    assert costs["cv_adapted"] >= 1


# ── Free tier: weekly refill + consumption ───────────────────────────


async def test_free_user_gets_weekly_refill_and_consumes(db_session, user):
    await activate_subscription(db_session, user, "free", source="signup_bonus")
    await db_session.commit()

    bal = await credits.get_balance(db_session, user.id)
    assert bal["balance"] == 2  # first weekly refill on account creation

    can, _, cid = await credits.check_credits(db_session, user.id, "cv_base", 1)
    assert can and cid

    account = await credits.consume_credits(db_session, user.id, "cv_base", 1)
    assert account.balance == 1

    # Second consumption fails with NotEnoughCreditsError? No — 1 remains:
    account2 = await credits.consume_credits(db_session, user.id, "cv_adapted", 1)
    assert account2.balance == 0

    with pytest.raises(NotEnoughCreditsError):
        await credits.consume_credits(db_session, user.id, "cv_base", 1)


async def test_free_weekly_refill_is_not_accumulative(db_session, user):
    await activate_subscription(db_session, user, "free", source="signup_bonus")
    await db_session.commit()

    # Use all credits.
    account = await credits.consume_credits(db_session, user.id, "cv_base", 2)
    assert account.balance == 0

    # Simulate the refill window passing: next weekly refill resets to 2,
    # never accumulates (would be 4 if it simply added).
    import datetime as dt

    account = await credits.get_or_create_credit_account(db_session, user.id)
    account.last_refill_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=8)
    await db_session.flush()
    bal = await credits.get_balance(db_session, user.id)
    assert bal["balance"] == 2


# ── Paid plans: period refill ────────────────────────────────────────


async def test_pro_activation_sets_tier_and_credits(db_session, user):
    sub = await activate_subscription(
        db_session, user, "pro", billing_cycle="monthly", source="purchase"
    )
    await db_session.commit()
    assert user.tier == "pro"
    assert sub.correlation_id

    bal = await credits.get_balance(db_session, user.id)
    assert bal["balance"] == 100  # pro credits_per_period


async def test_renewal_refills_credits_and_expires_leftover(db_session, user):
    """Credits must reset at each period boundary (renewal grants the new
    period allowance — leftover credits expire)."""
    import datetime as dt

    # 1. First activation → 100 credits.
    await activate_subscription(db_session, user, "pro", billing_cycle="monthly")
    await db_session.commit()
    bal = await credits.get_balance(db_session, user.id)
    assert bal["balance"] == 100

    # 2. Spend some credits.
    account = await credits.consume_credits(db_session, user.id, "cv_base", 40)
    assert account.balance == 60

    # 3. Simulate renewal (period turns): the refill anchor is cleared and
    #    the balance resets to the new period allowance (not 60+100).
    account.last_refill_at = None
    await db_session.flush()
    bal = await credits.get_balance(db_session, user.id)
    assert bal["balance"] == 100


async def test_auto_renew_process_grants_new_period_credits(db_session, user):
    """process_expired_subscriptions renews auto-renew subs and the new
    period's credits become available (old balance replaced)."""
    import datetime as dt

    sub = await activate_subscription(
        db_session, user, "max", source="admin", auto_renew=True
    )
    await db_session.commit()
    bal = await credits.get_balance(db_session, user.id)
    assert bal["balance"] == 500

    # Use some credits and force the period to end.
    account = await credits.consume_credits(db_session, user.id, "cv_base", 200)
    assert account.balance == 300
    sub.period_end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    await db_session.flush()

    changed = await process_expired_subscriptions(db_session)
    await db_session.commit()
    assert changed == 1
    assert sub.status == "active"

    # New period → new allowance (500), not 300 carried over.
    bal = await credits.get_balance(db_session, user.id)
    assert bal["balance"] == 500


# ── Gating ───────────────────────────────────────────────────────────


async def test_free_user_cannot_use_pipeline(db_session, user):
    await activate_subscription(db_session, user, "free")
    await db_session.commit()
    assert not await can_use_feature(db_session, user, "pipeline")
    access = await get_user_access(db_session, user)
    assert "pipeline" not in access["features"]


async def test_max_user_can_use_pipeline_and_quota(db_session, user):
    await activate_subscription(db_session, user, "max")
    await db_session.commit()
    assert await can_use_feature(db_session, user, "pipeline")

    plan = await get_plan(db_session, "max")
    assert plan is not None
    assert await credits.check_quota(db_session, user.id, plan)
    await credits.consume_quota(db_session, user.id, plan)
    bal = await credits.get_balance(db_session, user.id)
    assert bal["quota_day_used"] == 1


# ── Admin auto-renewing max ──────────────────────────────────────────


async def test_admin_gets_auto_renewing_max(db_session):
    admin = await _admin(db_session)
    await ensure_admin_subscription(db_session, admin.id)
    await db_session.commit()

    access = await get_user_access(db_session, {"sub": admin.id, "role": "admin"})
    assert access["is_admin"]
    assert "pipeline" in access["features"]
    sub = access["subscription"]
    assert sub is not None and sub.plan_key == "max" and sub.auto_renew


# ── Expiration ───────────────────────────────────────────────────────


async def test_expired_subscription_downgrades_tier(db_session, user):
    sub = await activate_subscription(db_session, user, "pro")
    await db_session.commit()
    await expire_subscription(db_session, sub)
    await db_session.commit()
    assert user.tier == "free"
    assert sub.status == "expired"


async def test_process_expired_renews_auto_renew(db_session, user):
    sub = await activate_subscription(
        db_session, user, "max", source="admin", auto_renew=True
    )
    await db_session.commit()
    # Force expiry.
    import datetime as dt

    sub.period_end = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    await db_session.flush()

    changed = await process_expired_subscriptions(db_session)
    await db_session.commit()
    assert changed == 1
    assert sub.status == "active"  # auto-renew keeps it active
    assert user.tier == "max"
