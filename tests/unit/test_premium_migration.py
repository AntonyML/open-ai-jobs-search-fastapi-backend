"""Tests for the legacy ``premium`` tier migration guard (plan.md §2.7).

The migration is a safety net: in the dev DB there are 0 ``premium`` users
(13 free · 1 pro · 1 max), so the script must be a clean no-op there, and
when legacy rows exist it must map them to a real plan without ever
duplicating subscriptions.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, User, UserSubscription
from tests.unit.plan_helpers import seed_test_plans
from app.services.premium_migration import DEFAULT_TARGET_PLAN, migrate_premium_tier
from app.services.subscriptions import activate_subscription


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


async def _make_user(db: AsyncSession, email: str, tier: str) -> User:
    u = User(id=email.split("@")[0], email=email, hashed_password="x", role="client", tier=tier)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _count_subs(db: AsyncSession, user_id: str) -> int:
    rows = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
    )
    return len(list(rows.scalars().all()))


# ── No-op ────────────────────────────────────────────────────────────


async def test_noop_when_no_premium_users(db_session):
    """With 0 premium users nothing changes and nothing is created."""
    await _make_user(db_session, "free@example.com", "free")
    await _make_user(db_session, "pro@example.com", "pro")
    await _make_user(db_session, "max@example.com", "max")
    await db_session.commit()

    result = await migrate_premium_tier(db_session)
    await db_session.commit()

    assert result.found == 0
    assert result.changed is False
    assert result.migrated == 0
    assert result.subscriptions_created == 0
    tiers = (await db_session.execute(select(User.tier))).scalars().all()
    assert sorted(tiers) == ["free", "max", "pro"]


# ── Migration ────────────────────────────────────────────────────────


async def test_migrates_premium_user_and_creates_subscription(db_session):
    user = await _make_user(db_session, "legacy@example.com", "premium")

    result = await migrate_premium_tier(db_session)
    await db_session.commit()

    await db_session.refresh(user)
    assert result.found == 1
    assert result.subscriptions_created == 1
    assert result.migrated == 1
    assert user.tier == DEFAULT_TARGET_PLAN

    subs = (
        await db_session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id))
    ).scalars().all()
    assert len(subs) == 1
    assert subs[0].plan_key == DEFAULT_TARGET_PLAN
    assert subs[0].status == "active"
    assert subs[0].source == "migration"
    assert subs[0].auto_renew is True


async def test_second_run_is_idempotent_no_duplicate_subs(db_session):
    """Running twice must never create a second subscription."""
    user = await _make_user(db_session, "legacy@example.com", "premium")

    first = await migrate_premium_tier(db_session)
    await db_session.commit()
    second = await migrate_premium_tier(db_session)
    await db_session.commit()

    await db_session.refresh(user)
    assert first.found == 1 and first.subscriptions_created == 1
    assert second.found == 0 and second.changed is False
    assert user.tier == DEFAULT_TARGET_PLAN
    assert await _count_subs(db_session, user.id) == 1


async def test_premium_with_existing_subscription_only_fixes_tier(db_session):
    """A premium user that already pays must not get a duplicate sub."""
    user = await _make_user(db_session, "legacy@example.com", "free")
    await activate_subscription(db_session, user, "pro", source="purchase")
    await db_session.commit()
    # Simulate the legacy state: paying user whose tier drifted to premium.
    user.tier = "premium"
    await db_session.commit()

    result = await migrate_premium_tier(db_session)
    await db_session.commit()

    await db_session.refresh(user)
    assert result.found == 1
    assert result.tier_only == 1
    assert result.subscriptions_created == 0
    assert user.tier == DEFAULT_TARGET_PLAN
    assert await _count_subs(db_session, user.id) == 1


async def test_custom_target_plan(db_session):
    user = await _make_user(db_session, "legacy@example.com", "premium")

    result = await migrate_premium_tier(db_session, target="pro")
    await db_session.commit()

    await db_session.refresh(user)
    assert result.found == 1 and result.subscriptions_created == 1
    assert user.tier == "pro"
    subs = (
        await db_session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id))
    ).scalars().all()
    assert len(subs) == 1 and subs[0].plan_key == "pro"


async def test_missing_target_plan_skips_and_leaves_user_untouched(db_session):
    """If the target plan does not exist, don't break anything — keep the
    premium tier visible so the operator fixes the catalog first."""
    user = await _make_user(db_session, "legacy@example.com", "premium")

    result = await migrate_premium_tier(db_session, target="ghost-plan")
    await db_session.commit()

    await db_session.refresh(user)
    assert result.found == 1
    assert result.skipped_missing_plan == 1
    assert result.migrated == 0
    assert user.tier == "premium"  # untouched
    assert await _count_subs(db_session, user.id) == 0
