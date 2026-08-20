"""Canonical credit-action catalog + effective-cost storage (plan.md §8.2).

Single source of truth for which actions consume AI credits and at what
cost.  Everything derives from :data:`CREDIT_ACTION_CATALOG`:

- the validation set for admin writes (unknown keys → error, never silent),
- the defaults used when a table row is missing,
- the drift test: every action passed to ``enforce_action_gate`` must exist
  here (forward) and catalog actions without consumers warn (reverse).

Effective costs live in ``credit_cost_config`` (typed table: ``CHECK cost
>= 0``, audit columns, optimistic ``version``) — the ``app_config
['credit_costs']`` JSON blob was migrated away in
``alembic/versions/..._credit_cost_config.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CreditCostConfig

# Admin UI sections — i18n keys ``adminPlans.costGroup.<key>`` (client side).
CREDIT_COST_GROUPS: tuple[str, ...] = ("cv", "job_search", "growth", "quality")


@dataclass(frozen=True)
class CreditActionSpec:
    """Metadata for one billable action.

    ``feature_gate`` is the plan feature required to run the action (None =
    available to every plan).  Client i18n keys follow the fixed convention
    ``adminPlans.cost.<key>`` (label) and ``adminPlans.costHint.<key>``
    (hint), so no per-spec key fields are needed.
    """

    key: str
    group: str
    default_cost: int
    feature_gate: str | None = None


# ── The catalog (append here when a new billable action ships) ─────────
CREDIT_ACTION_CATALOG: tuple[CreditActionSpec, ...] = (
    CreditActionSpec("cv_base", "cv", default_cost=1, feature_gate="cv_base"),
    CreditActionSpec("cv_adapted", "cv", default_cost=1, feature_gate="cv_adapted"),
    CreditActionSpec("rank", "job_search", default_cost=1, feature_gate="pipeline"),
    CreditActionSpec("apply", "job_search", default_cost=1, feature_gate="pipeline"),
    CreditActionSpec("interview", "job_search", default_cost=1, feature_gate="pipeline"),
    CreditActionSpec("expand", "growth", default_cost=1, feature_gate="expand"),
    CreditActionSpec("upskill", "growth", default_cost=1, feature_gate="upskill"),
    CreditActionSpec("verify", "quality", default_cost=1, feature_gate=None),
)

CATALOG_BY_KEY: dict[str, CreditActionSpec] = {s.key: s for s in CREDIT_ACTION_CATALOG}
CATALOG_KEYS: frozenset[str] = frozenset(CATALOG_BY_KEY)


def compute_backfill(legacy: dict[str, Any] | None) -> dict[str, int]:
    """Map the legacy ``app_config['credit_costs']`` JSON to effective costs.

    Migration helper (alembic b6c7d8e9f0a1): valid ``int >= 0`` values are
    preserved (0 = free), anything else (missing / string / negative) falls
    back to the catalog default.  Deterministic and idempotent — always
    returns one entry per catalog action.
    """
    legacy = legacy if isinstance(legacy, dict) else {}
    out: dict[str, int] = {}
    for spec in CREDIT_ACTION_CATALOG:
        raw = legacy.get(spec.key)
        out[spec.key] = raw if isinstance(raw, int) and raw >= 0 else spec.default_cost
    return out


class CreditCostConflictError(Exception):
    """Concurrent admin edit detected — map to HTTP 409."""


def _coerce_cost(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be an integer")
    if value < 0:
        raise ValueError("must be >= 0 (0 = free)")
    return value


async def get_effective_costs(db: AsyncSession) -> dict[str, int]:
    """Effective cost per catalog action: table value or spec default."""
    rows = (await db.execute(select(CreditCostConfig))).scalars().all()
    stored = {r.action: r.cost for r in rows}
    return {s.key: stored.get(s.key, s.default_cost) for s in CREDIT_ACTION_CATALOG}


async def get_action_cost(db: AsyncSession, action: str) -> int:
    """Effective cost for one action (used by the access gate)."""
    spec = CATALOG_BY_KEY.get(action)
    if spec is None:
        return 0
    return (await get_effective_costs(db)).get(action, spec.default_cost)


async def get_catalog(db: AsyncSession) -> dict[str, Any]:
    """Rich admin payload: groups + every catalog action with effective cost
    and current ``version`` (for optimistic locking on PUT)."""
    rows = (await db.execute(select(CreditCostConfig))).scalars().all()
    stored = {r.action: r for r in rows}
    return {
        "groups": list(CREDIT_COST_GROUPS),
        "actions": [
            {
                "key": spec.key,
                "group": spec.group,
                "cost": stored[spec.key].cost if spec.key in stored else spec.default_cost,
                "default_cost": spec.default_cost,
                "feature_gate": spec.feature_gate,
                "version": stored[spec.key].version if spec.key in stored else 1,
            }
            for spec in CREDIT_ACTION_CATALOG
        ],
    }


async def set_effective_costs(
    db: AsyncSession,
    costs: dict[str, Any],
    *,
    updated_by: str | None = None,
    expected_versions: dict[str, int] | None = None,
) -> dict[str, int]:
    """Persist the admin calibration (strict — plan.md §8.2).

    Raises:
        ValueError: any key is unknown / not an int / < 0 — nothing written.
        CreditCostConflictError: ``expected_versions`` provided and a row's
            version differs (concurrent edit, HTTP 409).

    Only catalog actions are writable; unknown keys are never dropped
    silently.  Returns the new effective costs.
    """
    invalid: list[str] = []
    for key, value in costs.items():
        if key not in CATALOG_BY_KEY:
            invalid.append(f"'{key}': unknown action")
            continue
        try:
            _coerce_cost(value)
        except ValueError as exc:
            invalid.append(f"'{key}': {exc}")
    if invalid:
        raise ValueError("Invalid credit costs: " + "; ".join(invalid))

    rows = (await db.execute(select(CreditCostConfig))).scalars().all()
    existing = {r.action: r for r in rows}
    now = datetime.now(UTC)

    for key, value in costs.items():
        row = existing.get(key)
        if expected_versions is not None:
            expected = expected_versions.get(key)
            if expected is not None:
                # A missing row's baseline version is 1 (its value on insert);
                # any other expectation means a concurrent edit/delete.
                stale = (row is None and expected != 1) or (row is not None and row.version != expected)
                if stale:
                    raise CreditCostConflictError(f"Concurrent edit detected for '{key}': reload and retry.")
        cost = int(value)
        if row is None:
            db.add(
                CreditCostConfig(
                    action=key,
                    cost=cost,
                    updated_by=updated_by,
                    version=1,
                    updated_at=now,
                )
            )
        else:
            row.cost = cost
            row.updated_by = updated_by
            row.updated_at = now
            row.version += 1
    await db.flush()
    return await get_effective_costs(db)
