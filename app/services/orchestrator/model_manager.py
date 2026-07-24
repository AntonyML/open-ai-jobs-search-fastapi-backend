"""Model Manager — tracks model states (READY, BUSY, COOLDOWN, DISABLED).

Responsibilities:
- Model registry with priorities within each provider
- Cost and context window tracking
- State machine (READY -> BUSY -> COOLDOWN -> READY/DISABLED)
- Smart model selection for failover within a provider
"""

from __future__ import annotations


from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import timedelta

from app.db.models import ModelHealth as ModelHealthModel
from app.core.logging import get_logger

logger = get_logger(__name__)

# Default model priorities within a provider (lower = higher priority)
DEFAULT_MODEL_PRIORITIES: dict[str, dict[str, int]] = {
    "anthropic": {
        "claude-sonnet-4-20250514": 1,
        "claude-sonnet-4-5": 2,
        "claude-opus-4-1": 3,
        "claude-haiku-4-5": 4,
        "claude-3-5-haiku-20241022": 5,
    },
    "openai": {
        "gpt-4.1": 1,
        "gpt-4o": 2,
        "gpt-4o-mini": 3,
    },
    "nvidia_nim": {
        "meta/llama-3.3-70b-instruct": 1,
        "meta/llama-3.1-70b-instruct": 2,
        "mistralai/mistral-large": 3,
    },
}

# Model state constants
STATE_READY = "READY"
STATE_BUSY = "BUSY"
STATE_COOLDOWN = "COOLDOWN"
STATE_DISABLED = "DISABLED"

ALL_STATES = {STATE_READY, STATE_BUSY, STATE_COOLDOWN, STATE_DISABLED}

# Cooldown for COOLDOWN state
MODEL_COOLDOWN_SECONDS = 30


async def get_or_create_model_health(
    db: AsyncSession,
    user_id: str,
    provider: str,
    model_name: str,
) -> ModelHealthModel:
    """Get the ModelHealth row, creating it with defaults if missing."""
    result = await db.execute(
        select(ModelHealthModel).where(
            ModelHealthModel.user_id == user_id,
            ModelHealthModel.provider == provider,
            ModelHealthModel.model_name == model_name,
        )
    )
    health = result.scalar_one_or_none()

    if health is None:
        # Determine default priority
        provider_priorities = DEFAULT_MODEL_PRIORITIES.get(provider, {})
        priority = provider_priorities.get(model_name, 5)

        health = ModelHealthModel(
            user_id=user_id,
            provider=provider,
            model_name=model_name,
            priority=priority,
            state=STATE_READY,
        )
        db.add(health)
        await db.flush()
        await db.refresh(health)

    return health


async def mark_model_busy(
    db: AsyncSession,
    user_id: str,
    provider: str,
    model_name: str,
) -> None:
    """Mark a model as BUSY (currently executing a request)."""
    health = await get_or_create_model_health(db, user_id, provider, model_name)
    health.state = STATE_BUSY
    await db.flush()


async def mark_model_completed(
    db: AsyncSession,
    user_id: str,
    provider: str,
    model_name: str,
    latency_ms: int | None = None,
    success: bool = True,
) -> None:
    """Mark a model as READY after completing a request.

    Updates rolling metrics (latency, success rate).
    """
    health = await get_or_create_model_health(db, user_id, provider, model_name)
    health.state = STATE_READY
    health.total_calls += 1

    if latency_ms is not None:
        # Exponential moving average for latency
        if health.average_latency_ms is None:
            health.average_latency_ms = float(latency_ms)
        else:
            health.average_latency_ms = (
                0.9 * health.average_latency_ms + 0.1 * latency_ms
            )

    # Update success rate (exponential moving average)
    if success:
        health.average_success_rate = min(
            1.0, health.average_success_rate + 0.05
        )
    else:
        health.average_success_rate = max(
            0.0, health.average_success_rate - 0.1
        )

    await db.flush()


async def mark_model_failed(
    db: AsyncSession,
    user_id: str,
    provider: str,
    model_name: str,
    error_code: str,
    error_message: str,
) -> None:
    """Mark a model as COOLDOWN or DISABLED after a failure."""
    health = await get_or_create_model_health(db, user_id, provider, model_name)
    health.state = STATE_COOLDOWN
    health.cooldown_until = datetime.now(timezone.utc).replace(
        microsecond=0
    ) + timedelta(seconds=MODEL_COOLDOWN_SECONDS)
    health.last_error = error_message[:500]
    health.last_error_code = error_code
    health.total_calls += 1
    health.average_success_rate = max(0.0, health.average_success_rate - 0.1)

    # If it's an auth error, disable the model immediately
    if error_code in ("auth_error",):
        health.state = STATE_DISABLED

    await db.flush()


async def get_available_models(
    db: AsyncSession,
    user_id: str,
    provider: str,
) -> list[ModelHealthModel]:
    """Get all healthy/available models for a provider, sorted by priority.

    Returns:
        List of ModelHealth rows in READY state, sorted by priority.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ModelHealthModel).where(
            ModelHealthModel.user_id == user_id,
            ModelHealthModel.provider == provider,
        ).order_by(ModelHealthModel.priority)
    )
    all_models = list(result.scalars().all())

    available = []
    for m in all_models:
        if m.state == STATE_DISABLED:
            continue
        if m.state == STATE_COOLDOWN:
            if m.cooldown_until and m.cooldown_until > now:
                continue
            # Cooldown expired, mark as ready
            m.state = STATE_READY
            m.cooldown_until = None
        available.append(m)

    if available:
        await db.flush()

    return available


async def get_model_health_status(
    db: AsyncSession,
    user_id: str,
    provider: str | None = None,
) -> list[ModelHealthModel]:
    """Get health status for all models, optionally filtered by provider."""
    query = select(ModelHealthModel).where(
        ModelHealthModel.user_id == user_id,
    )
    if provider:
        query = query.where(ModelHealthModel.provider == provider)
    query = query.order_by(
        ModelHealthModel.provider,
        ModelHealthModel.priority,
    )

    result = await db.execute(query)
    return list(result.scalars().all())


async def set_model_priority(
    db: AsyncSession,
    user_id: str,
    provider: str,
    model_name: str,
    priority: int,
) -> ModelHealthModel:
    """Set the priority for a model (lower = higher priority)."""
    health = await get_or_create_model_health(db, user_id, provider, model_name)
    health.priority = priority
    await db.flush()
    await db.refresh(health)
    return health


async def set_model_state(
    db: AsyncSession,
    user_id: str,
    provider: str,
    model_name: str,
    state: str,
) -> ModelHealthModel:
    """Manually set a model's state (for admin control)."""
    if state not in ALL_STATES:
        raise ValueError(f"Invalid model state: {state}. Must be one of {ALL_STATES}")

    health = await get_or_create_model_health(db, user_id, provider, model_name)
    health.state = state
    if state != STATE_COOLDOWN:
        health.cooldown_until = None
    await db.flush()
    await db.refresh(health)
    return health
