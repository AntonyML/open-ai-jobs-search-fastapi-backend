"""Tests for DB-driven plan usage caps (Fase E — tiers.py migration to plans catalog)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Plan
from app.db.session import get_db


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def api_client(db_session):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _token(user_id: str, role: str = "client", tier: str = "free") -> str:
    from app.core.security import create_access_token

    return create_access_token(subject=user_id, role=role, tier=tier)


async def _seed_free_plan(db: AsyncSession) -> None:
    db.add(
        Plan(
            id="p-free",
            key="free",
            name="Free",
            price_monthly_usd=0.0,
            price_yearly_usd=0.0,
            credits_per_period=2,
            max_apply_count=5,
            max_prepare_count=5,
            max_rank_iterations=3,
            max_track_count=5,
            features=[],
            is_active=True,
        )
    )
    await db.commit()


# ── Plan.limits property ─────────────────────────────────────────────


def test_plan_limits_property_mirrors_legacy_tier_shape():
    plan = Plan(
        key="free",
        name="Free",
        max_apply_count=5,
        max_prepare_count=5,
        max_rank_iterations=3,
        max_track_count=5,
    )
    assert plan.limits == {
        "max_apply_count": 5,
        "max_prepare_count": 5,
        "max_rank_iterations": 3,
        "max_track_count": 5,
        "expand_locked": False,
        "upskill_locked": False,
    }


def test_plan_limits_property_defaults_are_paid_style():
    plan = Plan(key="pro", name="Pro")
    assert plan.limits["max_apply_count"] == 1000
    assert plan.limits["max_track_count"] == 1000
    assert plan.limits["max_rank_iterations"] == 100
    assert plan.limits["expand_locked"] is False


# ── /users/usage (DB-driven limits) ──────────────────────────────────


def test_usage_returns_db_plan_caps_for_free(api_client, db_session):
    asyncio.run(_seed_free_plan(db_session))
    resp = api_client.get("/api/v1/users/usage", headers={"Authorization": f"Bearer {_token('u-1')}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["limits"] == {
        "max_apply_count": 5,
        "max_prepare_count": 5,
        "max_rank_iterations": 3,
        "max_track_count": 5,
    }
    assert body["usage"]["applications"] == 0


def test_usage_falls_back_to_paid_defaults_when_plan_row_missing(api_client):
    resp = api_client.get("/api/v1/users/usage", headers={"Authorization": f"Bearer {_token('u-1', tier='pro')}"})
    assert resp.status_code == 200
    assert resp.json()["limits"] == {
        "max_apply_count": 1000,
        "max_prepare_count": 1000,
        "max_rank_iterations": 100,
        "max_track_count": 1000,
    }


# ── Public catalog exposes caps ──────────────────────────────────────


def test_catalog_exposes_plan_caps(api_client, db_session):
    asyncio.run(_seed_free_plan(db_session))
    resp = api_client.get("/api/v1/public/catalog")
    assert resp.status_code == 200
    free_plan = next(p for p in resp.json()["plans"] if p["key"] == "free")
    assert free_plan["max_apply_count"] == 5
    assert free_plan["max_track_count"] == 5
    assert free_plan["max_rank_iterations"] == 3
    assert free_plan["expand_locked"] is False
