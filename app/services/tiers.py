"""Legacy tier / usage-limit compatibility layer.

DEPRECATED.  The source of truth for access and usage is now:

- ``app.services.plans`` — the DB-backed plan catalog (free / pro / max),
  credit allowances, and per-action credit costs.
- ``app.services.credits`` — the credit ledger, refills and the
  daily / weekly quotas that Max enforces.
- ``app.api.deps.require_max_or_admin`` — gates the job-search pipeline
  to ``max`` plan users (or the admin).

This module only survives so the usage widget (``GET /users/usage``) and
the legacy count guards in a few routers keep returning sensible values
while the frontend migrates.  Prefer plan + quota data over these
hardcoded numbers going forward.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

# ── Free plan (limited pipeline usage) ───────────────────────────────
FREE_TIER_LIMITS: dict[str, int | bool | str] = {
    "max_rank_iterations": 3,  # Limited ranking iterations
    "max_apply_count": 5,  # Max 5 applications
    "max_prepare_count": 5,  # Max 5 prepared docs
    "max_track_count": 5,  # Max 5 tracked jobs
    "expand_locked": True,  # Expand step locked
    "upskill_locked": True,  # Upskill step locked
}

# ── Paid plans (pro / max / legacy premium) — no artificial caps ────
PAID_TIER_LIMITS: dict[str, int | bool | str] = {
    "max_rank_iterations": 100,
    "max_apply_count": 1000,
    "max_prepare_count": 1000,
    "max_track_count": 1000,
    "expand_locked": False,
    "upskill_locked": False,
}

# Map the actual plan keys onto limits.  ``premium`` is kept as an alias
# for any legacy rows that still carry the old tier value.
TIER_LIMITS: dict[str, dict[str, int | bool | str]] = {
    "free": FREE_TIER_LIMITS,
    "pro": PAID_TIER_LIMITS,
    "max": PAID_TIER_LIMITS,
    "premium": PAID_TIER_LIMITS,
}


def get_tier_limits(tier: str) -> dict[str, int | bool | str]:
    """Return the usage limits for a given tier / plan key.

    Paid plans (pro / max / premium) get the full allowance; anything
    unknown falls back to the ``free`` limits.
    """
    return TIER_LIMITS.get(tier, FREE_TIER_LIMITS)


async def get_user_tier_limits(
    db: AsyncSession,
    user_id: str,
) -> dict[str, int | bool | str]:
    """Fetch a user's tier / plan and return the corresponding limits.

    Args:
        db: Database session.
        user_id: ID of the user.

    Returns:
        A dict of limit keys and their values.
    """
    result = await db.execute(select(User.tier).where(User.id == user_id))
    tier = result.scalar_one_or_none() or "free"
    return get_tier_limits(tier)
