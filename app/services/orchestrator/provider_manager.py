"""Provider Manager — tracks provider health, priorities, cooldowns, and failover.

Responsibilities:
- Provider registry with configurable priority tiers
- Health monitoring (success rate, latency, error tracking)
- Automatic cooldown on repeated failures / rate limits
- Health score calculation (0.0 = dead, 1.0 = perfect)
- Provider selection for failover
"""

from __future__ import annotations


import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderHealth as ProviderHealthModel
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Priority tiers (lower = higher priority) ─────────────────────────
# These are defaults; users can override via the API or DB.
# The orchestrator tries providers in priority order, then falls back
# to the next tier when all providers in the current tier fail.

DEFAULT_PROVIDER_PRIORITIES: dict[str, int] = {
    "anthropic": 10,
    "openai": 20,
    "nvidia_nim": 15,
    "lm_studio": 40,
    "ollama": 50,
}

# Cooldown durations (in seconds) based on error type
COOLDOWN_RATE_LIMIT: int = 60       # 1 minute for 429s
COOLDOWN_TIMEOUT: int = 30          # 30 seconds for timeouts
COOLDOWN_SERVER_ERROR: int = 120    # 2 minutes for 5xx
COOLDOWN_AUTH_ERROR: int = 600      # 10 minutes for auth failures
COOLDOWN_CONSECUTIVE_FLOOR: int = 10  # Minimum cooldown per consecutive failure

# Health score decay
HEALTH_SCORE_DECAY: float = 0.1     # How much each failure impacts the score
HEALTH_SCORE_RECOVERY: float = 0.02 # How much each success restores the score
HEALTH_SCORE_MIN: float = 0.0
HEALTH_SCORE_MAX: float = 1.0
HEALTH_SCORE_DEGRADED_THRESHOLD: float = 0.5  # Below this = degraded
HEALTH_SCORE_DISABLE_THRESHOLD: float = 0.1   # Below this = disabled

# Max consecutive failures before auto-disabling
MAX_CONSECUTIVE_FAILURES: int = 5


async def get_or_create_provider_health(
    db: AsyncSession,
    user_id: str,
    provider: str,
) -> ProviderHealthModel:
    """Get the ProviderHealth row, creating it with defaults if missing."""
    result = await db.execute(
        select(ProviderHealthModel).where(
            ProviderHealthModel.user_id == user_id,
            ProviderHealthModel.provider == provider,
        )
    )
    health = result.scalar_one_or_none()

    if health is None:
        priority = DEFAULT_PROVIDER_PRIORITIES.get(provider, 50)
        health = ProviderHealthModel(
            user_id=user_id,
            provider=provider,
            priority=priority,
        )
        db.add(health)
        await db.flush()
        await db.refresh(health)

    return health


async def record_success(
    db: AsyncSession,
    user_id: str,
    provider: str,
    latency_ms: int | None = None,
) -> None:
    """Record a successful LLM call for a provider."""
    health = await get_or_create_provider_health(db, user_id, provider)

    # Update metrics
    health.total_calls += 1
    health.success_count += 1
    health.consecutive_failures = 0
    health.status = "healthy"
    health.cooldown_until = None

    if latency_ms is not None:
        health.last_latency_ms = latency_ms

    # Recover health score
    health.health_score = min(
        HEALTH_SCORE_MAX,
        health.health_score + HEALTH_SCORE_RECOVERY,
    )

    await db.flush()


async def record_failure(
    db: AsyncSession,
    user_id: str,
    provider: str,
    error_code: str,
    error_message: str,
    latency_ms: int | None = None,
) -> None:
    """Record a failed LLM call and update provider health."""
    health = await get_or_create_provider_health(db, user_id, provider)

    health.total_calls += 1
    health.failure_count += 1
    health.consecutive_failures += 1
    health.last_error = error_message[:500]
    health.last_error_code = error_code

    if latency_ms is not None:
        health.last_latency_ms = latency_ms

    # Track specific error types
    if error_code == "rate_limit":
        health.rate_limit_count += 1
    elif error_code == "timeout":
        health.timeout_count += 1

    # Decay health score
    health.health_score = max(
        HEALTH_SCORE_MIN,
        health.health_score - HEALTH_SCORE_DECAY * health.consecutive_failures,
    )

    # Determine cooldown
    cooldown_seconds = _calculate_cooldown(
        error_code, health.consecutive_failures
    )
    health.cooldown_until = datetime.now(timezone.utc) + timedelta(
        seconds=cooldown_seconds
    )

    # Auto-disable if too many consecutive failures
    if health.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        health.status = "disabled"
        logger.warning(
            "Provider %s disabled after %d consecutive failures",
            provider, health.consecutive_failures,
        )
    elif health.health_score < HEALTH_SCORE_DISABLE_THRESHOLD:
        health.status = "disabled"
    elif health.health_score < HEALTH_SCORE_DEGRADED_THRESHOLD:
        health.status = "degraded"
    else:
        health.status = "cooldown"

    logger.info(
        "Provider %s status=%s health_score=%.2f cooldown=%ds consecutive=%d",
        provider, health.status, health.health_score,
        cooldown_seconds, health.consecutive_failures,
    )

    await db.flush()


def _calculate_cooldown(error_code: str, consecutive_failures: int) -> int:
    """Calculate cooldown duration based on error type and consecutive failures.

    Uses exponential backoff scaled by consecutive failures.
    """
    base_cooldown = {
        "rate_limit": COOLDOWN_RATE_LIMIT,
        "timeout": COOLDOWN_TIMEOUT,
        "server_error": COOLDOWN_SERVER_ERROR,
        "auth_error": COOLDOWN_AUTH_ERROR,
    }.get(error_code, COOLDOWN_SERVER_ERROR)

    # Exponential backoff: base * 2^(consecutive-1)
    backoff = base_cooldown * (2 ** (consecutive_failures - 1))
    # Cap at 30 minutes
    return min(backoff, 1800)


async def get_available_providers(
    db: AsyncSession,
    user_id: str,
    provider_whitelist: list[str] | None = None,
) -> list[ProviderHealthModel]:
    """Get all healthy/available providers, sorted by priority.

    Filters out:
    - Disabled providers
    - Providers still in cooldown
    - Providers not in whitelist (if specified)

    Returns:
        List of ProviderHealth rows sorted by priority (lowest first).
    """
    query = select(ProviderHealthModel).where(
        ProviderHealthModel.user_id == user_id,
    )

    if provider_whitelist:
        query = query.where(
            ProviderHealthModel.provider.in_(provider_whitelist)
        )

    result = await db.execute(query)
    all_providers = list(result.scalars().all())

    now = datetime.now(timezone.utc)

    # Filter to available providers
    available = []
    for p in all_providers:
        if p.status == "disabled":
            continue
        if p.cooldown_until and p.cooldown_until > now:
            continue
        available.append(p)

    # Sort by priority (lower = higher priority)
    available.sort(key=lambda p: p.priority)

    return available


async def get_provider_health_status(
    db: AsyncSession,
    user_id: str,
) -> list[ProviderHealthModel]:
    """Get full health status for all tracked providers."""
    result = await db.execute(
        select(ProviderHealthModel).where(
            ProviderHealthModel.user_id == user_id,
        ).order_by(ProviderHealthModel.priority)
    )
    return list(result.scalars().all())


async def set_provider_priority(
    db: AsyncSession,
    user_id: str,
    provider: str,
    priority: int,
) -> ProviderHealthModel:
    """Set the priority tier for a provider (lower = higher priority)."""
    health = await get_or_create_provider_health(db, user_id, provider)
    health.priority = priority
    await db.flush()
    await db.refresh(health)
    return health


async def reset_provider_health(
    db: AsyncSession,
    user_id: str,
    provider: str,
) -> ProviderHealthModel:
    """Reset health metrics for a provider back to defaults."""
    health = await get_or_create_provider_health(db, user_id, provider)
    health.status = "healthy"
    health.cooldown_until = None
    health.consecutive_failures = 0
    health.health_score = 1.0
    health.total_calls = 0
    health.success_count = 0
    health.failure_count = 0
    health.rate_limit_count = 0
    health.timeout_count = 0
    health.last_error = None
    health.last_error_code = None
    await db.flush()
    await db.refresh(health)
    return health
