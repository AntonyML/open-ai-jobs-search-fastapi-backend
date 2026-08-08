"""Tier / usage-limit constants and helper functions.

Defines what each tier (free / premium) can access.
Usage limits are checked on the backend when pipeline steps execute.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

# ── Free tier limits ────────────────────────────────────────────────
FREE_TIER_LIMITS: dict[str, int | bool | str] = {
    "max_providers": 1,  # Only 1 LLM provider
    "allow_nvidia_nim": False,  # Nvidia NIM locked
    "max_rank_iterations": 3,  # Limited ranking iterations
    "max_apply_count": 5,  # Max 5 applications
    "max_prepare_count": 5,  # Max 5 prepared docs
    "max_track_count": 5,  # Max 5 tracked jobs
    "expand_locked": True,  # Expand step locked
    "upskill_locked": True,  # Upskill step locked
}

# ── Premium tier — no artificial limits ────────────────────────────
PREMIUM_TIER_LIMITS: dict[str, int | bool | str] = {
    "max_providers": 10,
    "allow_nvidia_nim": True,
    "max_rank_iterations": 100,
    "max_apply_count": 1000,
    "max_prepare_count": 1000,
    "max_track_count": 1000,
    "expand_locked": False,
    "upskill_locked": False,
}

TIER_LIMITS: dict[str, dict[str, int | bool | str]] = {
    "free": FREE_TIER_LIMITS,
    "premium": PREMIUM_TIER_LIMITS,
}


def get_tier_limits(tier: str) -> dict[str, int | bool | str]:
    """Return the usage limits for a given tier.

    Defaults to ``free`` limits for unknown tiers.
    """
    return TIER_LIMITS.get(tier, FREE_TIER_LIMITS)


async def get_user_tier_limits(
    db: AsyncSession,
    user_id: str,
) -> dict[str, int | bool | str]:
    """Fetch a user's tier and return the corresponding limits.

    Args:
        db: Database session.
        user_id: ID of the user.

    Returns:
        A dict of limit keys and their values.
    """
    result = await db.execute(select(User.tier).where(User.id == user_id))
    tier = result.scalar_one_or_none() or "free"
    return get_tier_limits(tier)
