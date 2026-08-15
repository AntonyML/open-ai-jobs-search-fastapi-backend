"""Tests for the public catalog endpoint (app/api/v1/public.py).

The public endpoint must NOT require auth: it is served to visitors on the
landing page and /limits without a token, and returns the same catalog as the
authenticated /billing/catalog plus usage quotas and last_updated.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.public import get_public_catalog
from app.db.models import Base, Plan


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _seed_plans(db: AsyncSession) -> None:
    db.add(Plan(
        key="free",
        name="Free",
        price_monthly_usd=0.0,
        price_yearly_usd=0.0,
        credits_per_period=2,
        refill_cadence="weekly",
        refill_weekday=0,
        daily_quota=0,
        weekly_quota=0,
        features=["cv_base", "cv_adapted"],
        is_active=True,
        sort_order=10,
        updated_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    ))
    db.add(Plan(
        key="pro",
        name="Pro",
        price_monthly_usd=24.99,
        price_yearly_usd=249.0,
        credits_per_period=80,
        refill_cadence="period",
        daily_quota=0,
        weekly_quota=0,
        features=["cv_base", "cv_adapted"],
        is_active=True,
        sort_order=20,
        updated_at=datetime(2026, 8, 10, 9, 30, tzinfo=timezone.utc),
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_public_catalog_returns_plans_with_quotas(db_session):
    await _seed_plans(db_session)
    catalog = await get_public_catalog(db=db_session)

    assert len(catalog.plans) == 2
    pro = next(p for p in catalog.plans if p.key == "pro")
    assert pro.price_monthly_usd == 24.99
    assert pro.credits_per_period == 80
    assert pro.refill_weekday == 0
    assert pro.daily_quota == 0
    assert pro.weekly_quota == 0
    assert catalog.credit_costs.cv_base == 1


@pytest.mark.asyncio
async def test_public_catalog_last_updated_is_max(db_session):
    await _seed_plans(db_session)
    catalog = await get_public_catalog(db=db_session)
    # latest seeded updated_at among the plans
    assert catalog.last_updated is not None
    assert catalog.last_updated.year == 2026
    assert catalog.last_updated.month == 8
    assert catalog.last_updated.day == 10


@pytest.mark.asyncio
async def test_public_catalog_returns_503_when_no_active_plans(db_session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await get_public_catalog(db=db_session)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "no_plans_configured"
