"""Tests for the billing configuration singletons (Fase 0).

``topup_packs`` and ``billing_policy`` live in ``app_config`` following the
``credit_costs`` pattern: default values in code, defensive validation on
read, strict validation on write.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.admin import (
    get_admin_billing_policy,
    get_admin_topup_packs,
    put_admin_billing_policy,
    put_admin_topup_packs,
)
from app.db.models import AppConfig, Base
from app.services.billing_policy import (
    BILLING_POLICY_CONFIG_KEY,
    DEFAULT_BILLING_POLICY,
    get_billing_policy,
    set_billing_policy,
)
from app.services.topups import (
    DEFAULT_TOPUP_PACKS,
    TOPUP_PACKS_CONFIG_KEY,
    get_topup_packs,
    set_topup_packs,
)


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def _store(db, key: str, value) -> None:
    db.add(AppConfig(key=key, value=value))
    await db.flush()


# ── Top-up packs ─────────────────────────────────────────────────────


async def test_topup_packs_defaults(db_session):
    packs = await get_topup_packs(db_session)
    assert len(packs) == 2  # locked to exactly 2
    assert packs == DEFAULT_TOPUP_PACKS
    assert {p["credits"] for p in packs} == {50, 120}


async def test_set_topup_packs_roundtrip(db_session):
    custom = [{"price_usd": 4.99, "credits": 25}, {"price_usd": 14.99, "credits": 90}]
    packs = await set_topup_packs(db_session, custom)
    assert packs == custom
    assert await get_topup_packs(db_session) == custom


async def test_set_topup_packs_rejects_wrong_count(db_session):
    with pytest.raises(ValueError, match="Exactly 2"):
        await set_topup_packs(db_session, [DEFAULT_TOPUP_PACKS[0]])
    with pytest.raises(ValueError, match="Exactly 2"):
        await set_topup_packs(db_session, DEFAULT_TOPUP_PACKS + [{"price_usd": 1.0, "credits": 5}])


async def test_set_topup_packs_rejects_invalid_shape(db_session):
    bad = [{"price_usd": 0, "credits": 50}, {"price_usd": 19.99, "credits": 120}]
    with pytest.raises(ValueError, match="price_usd > 0"):
        await set_topup_packs(db_session, bad)
    bad_credits = [{"price_usd": 9.99, "credits": "50"}, {"price_usd": 19.99, "credits": 120}]
    with pytest.raises(ValueError, match="price_usd > 0"):
        await set_topup_packs(db_session, bad_credits)


async def test_get_topup_packs_falls_back_on_bad_stored(db_session):
    # A corrupted stored value must never break the catalog: fall back to defaults.
    await _store(db_session, TOPUP_PACKS_CONFIG_KEY, [{"price_usd": 9.99, "credits": 50}])
    assert await get_topup_packs(db_session) == DEFAULT_TOPUP_PACKS


# ── Billing policy ───────────────────────────────────────────────────


async def test_billing_policy_defaults(db_session):
    policy = await get_billing_policy(db_session)
    assert policy == DEFAULT_BILLING_POLICY
    assert policy["refund_credit_threshold"] == 16
    assert policy["annual_cooling_days"] == 14


async def test_set_billing_policy_roundtrip(db_session):
    custom = {"refund_credit_threshold": 8, "annual_cooling_days": 30}
    assert await set_billing_policy(db_session, custom) == custom
    assert await get_billing_policy(db_session) == custom


async def test_set_billing_policy_rejects_invalid(db_session):
    with pytest.raises(ValueError, match="refund_credit_threshold"):
        await set_billing_policy(db_session, {"refund_credit_threshold": -1, "annual_cooling_days": 14})
    with pytest.raises(ValueError, match="refund_credit_threshold"):
        await set_billing_policy(db_session, {"annual_cooling_days": 14})


async def test_get_billing_policy_merges_partial_stored(db_session):
    await _store(db_session, BILLING_POLICY_CONFIG_KEY, {"refund_credit_threshold": 8})
    policy = await get_billing_policy(db_session)
    assert policy["refund_credit_threshold"] == 8
    assert policy["annual_cooling_days"] == DEFAULT_BILLING_POLICY["annual_cooling_days"]


# ── Admin API wiring (Fase 4) ────────────────────────────────────────


def _admin_ctx() -> dict:
    return {"sub": "admin-1", "role": "admin"}


async def test_admin_topup_packs_endpoints(db_session):
    assert await get_admin_topup_packs(admin=_admin_ctx(), db=db_session) == DEFAULT_TOPUP_PACKS

    custom = {"packs": [{"price_usd": 4.99, "credits": 25}, {"price_usd": 14.99, "credits": 90}]}
    saved = await put_admin_topup_packs(payload=custom, admin=_admin_ctx(), db=db_session)
    assert saved == custom["packs"]
    assert await get_admin_topup_packs(admin=_admin_ctx(), db=db_session) == custom["packs"]


async def test_admin_topup_packs_rejects_bad_payload(db_session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await put_admin_topup_packs(payload={"packs": [DEFAULT_TOPUP_PACKS[0]]}, admin=_admin_ctx(), db=db_session)
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        await put_admin_topup_packs(payload={}, admin=_admin_ctx(), db=db_session)
    assert exc.value.status_code == 422


async def test_admin_billing_policy_endpoints(db_session):
    assert await get_admin_billing_policy(admin=_admin_ctx(), db=db_session) == DEFAULT_BILLING_POLICY

    custom = {"refund_credit_threshold": 8, "annual_cooling_days": 30}
    saved = await put_admin_billing_policy(payload=custom, admin=_admin_ctx(), db=db_session)
    assert saved == custom
    assert await get_admin_billing_policy(admin=_admin_ctx(), db=db_session) == custom


async def test_admin_billing_policy_rejects_bad_payload(db_session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await put_admin_billing_policy(
            payload={"refund_credit_threshold": -1, "annual_cooling_days": 14},
            admin=_admin_ctx(),
            db=db_session,
        )
    assert exc.value.status_code == 422


# ── Credit-cost admin API (plan.md §8.2, Fase 2) ────────────────────


async def test_admin_credit_costs_get_returns_rich_catalog(db_session):
    from app.api.v1.admin import get_admin_credit_costs
    from app.services.credit_costs import CREDIT_ACTION_CATALOG, CREDIT_COST_GROUPS

    result = await get_admin_credit_costs(admin=_admin_ctx(), db=db_session)
    assert result["groups"] == list(CREDIT_COST_GROUPS)
    assert len(result["actions"]) == len(CREDIT_ACTION_CATALOG)
    keys = {a["key"] for a in result["actions"]}
    assert keys == {s.key for s in CREDIT_ACTION_CATALOG}
    assert all("cost" in a and "version" in a and "feature_gate" in a for a in result["actions"])


async def test_admin_credit_costs_put_strict_422_on_unknown_key(db_session):
    from fastapi import HTTPException

    from app.api.v1.admin import put_admin_credit_costs
    from app.schemas.billing import CreditCostsUpdate

    with pytest.raises(HTTPException) as exc:
        await put_admin_credit_costs(
            payload=CreditCostsUpdate(costs={"cv_base": 1, "pipeline": 1}),
            admin=_admin_ctx(),
            db=db_session,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "invalid_credit_costs"
    assert "pipeline" in exc.value.detail["message"]  # never a silent drop
    # nothing persisted
    from app.services.credit_costs import get_catalog

    entry = next(a for a in (await get_catalog(db_session))["actions"] if a["key"] == "cv_base")
    assert entry["cost"] == 1 and entry["version"] == 1


async def test_admin_credit_costs_put_409_on_stale_version(db_session):
    from fastapi import HTTPException

    from app.api.v1.admin import put_admin_credit_costs
    from app.schemas.billing import CreditCostsUpdate

    await put_admin_credit_costs(payload=CreditCostsUpdate(costs={"cv_base": 3}), admin=_admin_ctx(), db=db_session)
    with pytest.raises(HTTPException) as exc:
        await put_admin_credit_costs(
            payload=CreditCostsUpdate(costs={"cv_base": 5}, expected_versions={"cv_base": 99}),
            admin=_admin_ctx(),
            db=db_session,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "credit_costs_conflict"


async def test_admin_credit_costs_put_success_records_updated_by(db_session):
    from sqlalchemy import select

    from app.api.v1.admin import put_admin_credit_costs
    from app.db.models import CreditCostConfig
    from app.schemas.billing import CreditCostsUpdate

    result = await put_admin_credit_costs(
        payload=CreditCostsUpdate(costs={"cv_base": 3, "verify": 0}),
        admin=_admin_ctx(),
        db=db_session,
    )
    by_key = {a["key"]: a for a in result["actions"]}
    assert by_key["cv_base"]["cost"] == 3
    assert by_key["verify"]["cost"] == 0  # 0 = free

    rows = (await db_session.execute(select(CreditCostConfig))).scalars().all()
    row = next(r for r in rows if r.action == "cv_base")
    assert row.updated_by == "admin-1"
    assert row.version == 1
