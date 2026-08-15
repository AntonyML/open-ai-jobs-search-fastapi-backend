"""Tests for the canonical credit-action catalog (plan.md §8.2, Fase 1).

Covers:
- catalog invariants,
- the drift test (forward = hard fail, reverse = warning only),
- the migration backfill helper (preserves values, seeds defaults, idempotent),
- strict table CRUD via ``set_effective_costs`` (no silent drops, 409 conflict),
- the legacy lenient path removed (all writes strict),
- the rich catalog payload (GET /admin/credit-costs).
"""

import pathlib
import re
import warnings

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, CreditCostConfig
from app.services.credit_costs import (
    CATALOG_BY_KEY,
    CATALOG_KEYS,
    CREDIT_ACTION_CATALOG,
    CREDIT_COST_GROUPS,
    CreditCostConflictError,
    compute_backfill,
    get_effective_costs,
    set_effective_costs,
)
from app.services import plans


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


# ── Catalog invariants ───────────────────────────────────────────────


def test_catalog_invariants():
    keys = [s.key for s in CREDIT_ACTION_CATALOG]
    assert len(keys) == len(set(keys)), "catalog keys must be unique"
    assert set(keys) == CATALOG_KEYS
    assert set(CATALOG_BY_KEY) == CATALOG_KEYS
    for spec in CREDIT_ACTION_CATALOG:
        assert spec.group in CREDIT_COST_GROUPS, f"{spec.key}: unknown group"
        assert spec.default_cost >= 0, f"{spec.key}: default_cost < 0"


# ── Drift test (the test that would have caught the 'pipeline' bug) ──


def _gate_actions_in_source() -> set[str]:
    """Scan the whole ``app/`` tree (api, services, workers, jobs) — not just
    ``app/api`` — so a future ``enforce_action_gate`` call from a background
    job or worker is still caught (plan.md §8.2, Fase 2)."""
    pattern = re.compile(
        r"enforce_action_gate\(\s*[^,]+,\s*[^,]+,\s*['\"]([a-z_]+)['\"]",
        re.DOTALL,
    )
    used: set[str] = set()
    for path in pathlib.Path("app").rglob("*.py"):
        used.update(pattern.findall(path.read_text(encoding="utf-8")))
    return used


def test_drift_forward_every_gate_action_exists_in_catalog():
    """Hard fail: every action passed to enforce_action_gate must be in the
    catalog — otherwise a billable action is invisible to the admin UI."""
    used = _gate_actions_in_source()
    assert used, "no enforce_action_gate calls found — pattern may have drifted"
    missing = used - CATALOG_KEYS
    assert not missing, (
        f"action(s) used in enforce_action_gate but missing from the catalog: "
        f"{sorted(missing)}. Add them to CREDIT_ACTION_CATALOG first."
    )


def test_drift_reverse_catalog_actions_without_consumers_warn_only():
    """Warning only (plan.md §8.2): the catalog may legitimately declare an
    action before its endpoint ships — never hard-fail that direction."""
    used = _gate_actions_in_source()
    orphaned = CATALOG_KEYS - used
    if orphaned:
        warnings.warn(
            f"catalog action(s) with no enforce_action_gate consumer yet: "
            f"{sorted(orphaned)}",
            stacklevel=1,
        )


# ── Migration backfill helper ────────────────────────────────────────


def test_backfill_preserves_values_and_seeds_defaults():
    legacy = {"cv_base": 3, "cv_adapted": 0, "rank": "5", "apply": -1}
    result = compute_backfill(legacy)
    assert result["cv_base"] == 3  # preserved
    assert result["cv_adapted"] == 0  # 0 = free, preserved
    assert result["rank"] == 1  # string -> default
    assert result["apply"] == 1  # negative -> default
    assert result["verify"] == 1  # missing -> default
    assert set(result) == CATALOG_KEYS  # complete — nothing dropped


def test_backfill_is_idempotent():
    legacy = {"cv_base": 3, "verify": 0}
    first = compute_backfill(legacy)
    assert compute_backfill(first) == first  # feeding the result back is stable
    assert compute_backfill(legacy) == compute_backfill(legacy)


def test_backfill_empty_and_non_dict():
    expected_defaults = {s.key: s.default_cost for s in CREDIT_ACTION_CATALOG}
    for junk in (None, "oops", [1, 2]):
        assert compute_backfill(junk) == expected_defaults


# ── Table CRUD (strict) ──────────────────────────────────────────────


async def test_get_effective_costs_returns_defaults_when_table_empty(db_session):
    costs = await get_effective_costs(db_session)
    assert costs == {s.key: s.default_cost for s in CREDIT_ACTION_CATALOG}


async def test_set_effective_costs_roundtrip(db_session):
    costs = {"cv_base": 3, "cv_adapted": 0, "verify": 5}
    result = await set_effective_costs(db_session, costs, updated_by="admin-1")
    assert result["cv_base"] == 3
    assert result["cv_adapted"] == 0  # 0 = free
    assert result["verify"] == 5
    # others untouched at default
    assert result["rank"] == 1
    assert await get_effective_costs(db_session) == result

    # second write on the same action bumps version (optimistic locking)
    result2 = await set_effective_costs(db_session, {"cv_base": 4}, updated_by="admin-2")
    assert result2["cv_base"] == 4

    rows = (await db_session.execute(select(CreditCostConfig))).scalars().all()
    by_action = {r.action: r for r in rows}
    assert by_action["cv_base"].version == 2  # 1 (insert) -> 2 (update)
    assert by_action["cv_base"].updated_by == "admin-2"
    assert "rank" not in by_action  # only written keys get rows (read falls back)


async def test_set_effective_costs_rejects_unknown_key(db_session):
    with pytest.raises(ValueError, match="pipeline"):
        await set_effective_costs(db_session, {"cv_base": 1, "pipeline": 1})
    # nothing was written
    assert (await get_effective_costs(db_session)) == {
        s.key: s.default_cost for s in CREDIT_ACTION_CATALOG
    }


async def test_set_effective_costs_rejects_negative_and_non_int(db_session):
    with pytest.raises(ValueError, match=">= 0"):
        await set_effective_costs(db_session, {"cv_base": -1})
    with pytest.raises(ValueError, match="integer"):
        await set_effective_costs(db_session, {"cv_base": "5"})
    with pytest.raises(ValueError, match="integer"):
        await set_effective_costs(db_session, {"cv_base": True})


async def test_set_effective_costs_conflict_on_stale_version(db_session):
    await set_effective_costs(db_session, {"cv_base": 3})
    with pytest.raises(CreditCostConflictError, match="cv_base"):
        await set_effective_costs(
            db_session, {"cv_base": 5}, expected_versions={"cv_base": 99}
        )
    # nothing written on conflict
    assert (await get_effective_costs(db_session))["cv_base"] == 3


async def test_set_effective_costs_no_conflict_with_current_version(db_session):
    await set_effective_costs(db_session, {"cv_base": 3})  # insert -> version 1
    result = await set_effective_costs(
        db_session, {"cv_base": 7}, expected_versions={"cv_base": 1}
    )
    assert result["cv_base"] == 7


# ── Legacy path is gone (plan.md §8.2, Fase 2) ─────────────────────


def test_plans_no_longer_exposes_legacy_set_credit_costs():
    """The lenient path was removed — all writes go through the strict
    ``set_effective_costs`` (422 on unknown keys, never a silent drop)."""
    assert not hasattr(plans, "set_credit_costs")


async def test_plans_get_credit_costs_reads_new_table(db_session):
    # seed via the strict service, read via the delegate
    await set_effective_costs(db_session, {"verify": 4})
    assert (await plans.get_credit_costs(db_session))["verify"] == 4
    assert (await plans.get_credit_costs(db_session))["cv_base"] == 1


# ── Rich catalog payload (GET /admin/credit-costs) ──────────────────


async def test_get_catalog_returns_groups_and_actions(db_session):
    from app.services.credit_costs import get_catalog

    catalog = await get_catalog(db_session)
    assert catalog["groups"] == list(CREDIT_COST_GROUPS)
    actions = {a["key"]: a for a in catalog["actions"]}
    assert set(actions) == CATALOG_KEYS
    for spec in CREDIT_ACTION_CATALOG:
        entry = actions[spec.key]
        assert entry["group"] == spec.group
        assert entry["default_cost"] == spec.default_cost
        assert entry["feature_gate"] == spec.feature_gate
        assert entry["cost"] == spec.default_cost  # no rows yet
        assert entry["version"] == 1  # baseline


async def test_get_catalog_reflects_written_costs_and_versions(db_session):
    from app.services.credit_costs import get_catalog

    await set_effective_costs(db_session, {"cv_base": 3})
    catalog = await get_catalog(db_session)
    entry = next(a for a in catalog["actions"] if a["key"] == "cv_base")
    assert entry["cost"] == 3
    assert entry["version"] == 1
    # second write bumps the version the client must echo back
    await set_effective_costs(db_session, {"cv_base": 5})
    catalog = await get_catalog(db_session)  # re-fetch — the old dict is stale
    entry = next(a for a in catalog["actions"] if a["key"] == "cv_base")
    assert entry["cost"] == 5
    assert entry["version"] == 2
