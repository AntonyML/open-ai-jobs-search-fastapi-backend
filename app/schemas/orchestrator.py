"""Pydantic schemas for the LLM orchestrator.

Response shapes for provider health, model health, queue status,
and execution job management.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Provider health schemas ────────────────────────────────────────


class ProviderHealthOut(BaseModel):
    """Health status for a single LLM provider."""

    provider: str
    status: str  # healthy, degraded, cooldown, disabled
    priority: int
    cooldown_until: datetime | None = None

    # Metrics
    total_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    rate_limit_count: int = 0
    timeout_count: int = 0
    consecutive_failures: int = 0
    health_score: float = 1.0
    last_latency_ms: int | None = None
    last_error: str | None = None
    last_error_code: str | None = None


class ProviderListOut(BaseModel):
    """List of provider health statuses."""

    providers: list[ProviderHealthOut]


# ── Model health schemas ────────────────────────────────────────────


class ModelHealthOut(BaseModel):
    """Health status for a single model within a provider."""

    provider: str
    model_name: str
    state: str  # READY, BUSY, COOLDOWN, DISABLED
    priority: int
    cost_rank: int = 5
    context_window: int | None = None
    cooldown_until: datetime | None = None

    # Metrics
    average_latency_ms: float | None = None
    average_success_rate: float = 1.0
    total_calls: int = 0
    last_error: str | None = None
    last_error_code: str | None = None


class ModelListOut(BaseModel):
    """List of model health statuses for a provider."""

    provider: str
    models: list[ModelHealthOut]


# ── Execution job schemas ────────────────────────────────────────────


class ExecutionJobOut(BaseModel):
    """Execution job status."""

    id: str
    user_id: str
    pipeline: str
    group_id: str | None = None
    description: str | None = None

    status: str
    provider: str | None = None
    model: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    last_error: str | None = None
    last_error_code: str | None = None

    started_at: datetime | None = None
    finished_at: datetime | None = None
    execution_time_ms: int | None = None

    worker_id: str | None = None
    created_at: datetime
    updated_at: datetime


class QueueStatusOut(BaseModel):
    """Overall queue status for a user."""

    paused: bool = False
    max_concurrency: int = 4
    active_workers: int = 0

    total_enqueued: int = 0
    total_completed: int = 0
    total_failed: int = 0
    total_cancelled: int = 0

    pending_jobs: list[ExecutionJobOut] = []
    running_jobs: list[ExecutionJobOut] = []
    recent_completed: list[ExecutionJobOut] = []


# ── Queue control schemas ───────────────────────────────────────────


class QueueControlRequest(BaseModel):
    """Control the execution queue."""

    action: str = Field(
        ...,
        description="One of: pause, resume, cancel, retry_failed",
        pattern="^(pause|resume|cancel|retry_failed)$",
    )
    job_id: str | None = Field(
        None,
        description="Specific job ID to cancel/retry. If omitted, applies to all.",
    )


class QueueControlResult(BaseModel):
    """Result of a queue control action."""

    action: str
    affected_jobs: int = 0
    message: str


# ── Error response ─────────────────────────────────────────────────


class OrchestratorError(BaseModel):
    """Error response from orchestrator operations."""

    error: str
    message: str
    details: dict[str, Any] | None = None
