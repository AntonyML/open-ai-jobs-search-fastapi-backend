"""Tests for ``GET /billing/status`` exposing ``next_reset_at`` (Fase 4).

The weekly quota bar on the billing page needs to know when the quota
windows reset.  ``next_quota_reset_at`` lives in ``app.services.credits``
(single source of truth shared with the 429 gate detail, plan.md §4) and
the status endpoint surfaces it via ``CreditStatusOut.next_reset_at``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.billing import get_billing_status
from app.db.models import Base, User
from app.services import credits
from tests.unit.plan_helpers import seed_test_plans


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
async def user(db_session):
    u = User(id="user-1", email="user@example.com", hashed_password="x", role="client", tier="free")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


def _client_ctx(user_id: str) -> dict:
    return {"sub": user_id, "role": "client"}


async def test_status_exposes_next_reset_at(db_session, user):
    """A fresh free user gets quota windows anchored now → next_reset_at ≈ +1d."""
    out = await get_billing_status(user=_client_ctx(user.id), db=db_session)
    assert out.next_reset_at is not None
    now = datetime.now(UTC)
    assert now < out.next_reset_at <= now + timedelta(days=8)
    assert out.quota_week_limit >= 0


async def test_next_quota_reset_at_helper(db_session, user):
    """Helper returns the earliest upcoming window reset (day < week)."""
    account = await credits.get_or_create_credit_account(db_session, user.id)
    now = datetime.now(UTC)
    account.quota_day_reset_at = now
    account.quota_week_reset_at = now

    # Day window wins: reset = day_start + 1 day.
    assert credits.next_quota_reset_at(account) == now + timedelta(days=1)

    # Only the week window exists → week_start + 7 days.
    account.quota_day_reset_at = None
    assert credits.next_quota_reset_at(account) == now + timedelta(days=7)

    # No windows at all → None.
    account.quota_week_reset_at = None
    assert credits.next_quota_reset_at(account) is None


async def test_next_quota_reset_at_skips_past_windows(db_session, user):
    """A window whose reset already passed is ignored in favor of a future one."""
    account = await credits.get_or_create_credit_account(db_session, user.id)
    now = datetime.now(UTC)
    account.quota_day_reset_at = now - timedelta(days=3)  # reset already passed
    account.quota_week_reset_at = now
    # Only the future week window counts → +7 days.
    assert credits.next_quota_reset_at(account) == now + timedelta(days=7)
