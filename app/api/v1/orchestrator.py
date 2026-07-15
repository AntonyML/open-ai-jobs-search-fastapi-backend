"""Orchestrator router — endpoints for managing the LLM execution queue.

Exposes monitoring and control endpoints:
- Queue status, pause, resume, cancel, retry
- Provider health metrics
- Model health metrics
- Individual job status
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.orchestrator import (
    ExecutionJobOut,
    ModelListOut,
    ProviderListOut,
    QueueControlRequest,
    QueueControlResult,
    QueueStatusOut,
)
from app.services.orchestrator.orchestrator_deps import get_orchestrator
from app.services.orchestrator import LLMOrchestrator

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


# ── Queue management ────────────────────────────────────────────────


@router.get("/queue", response_model=QueueStatusOut)
async def get_queue_status(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    orchestrator: LLMOrchestrator = Depends(get_orchestrator),
):
    """Get the current execution queue status."""
    return await orchestrator.get_queue_status(db, user["sub"])


@router.post("/queue/control", response_model=QueueControlResult)
async def control_queue(
    payload: QueueControlRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    orchestrator: LLMOrchestrator = Depends(get_orchestrator),
):
    """Control the execution queue: pause, resume, cancel, retry_failed.

    - pause: Stop processing new jobs from the queue.
    - resume: Resume processing and queue any pending jobs.
    - cancel: Cancel a specific job (by job_id) or all active jobs.
    - retry_failed: Retry a specific failed job (by job_id) or all failed jobs.
    """
    return await orchestrator.handle_queue_control(
        db=db,
        user_id=user["sub"],
        action=payload.action,
        job_id=payload.job_id,
    )


# ── Job status ──────────────────────────────────────────────────────


@router.get("/jobs/{job_id}", response_model=ExecutionJobOut | None)
async def get_execution_job(
    job_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    orchestrator: LLMOrchestrator = Depends(get_orchestrator),
):
    """Get the status of a specific execution job."""
    return await orchestrator.get_job(db, job_id=job_id)


# ── Provider health ─────────────────────────────────────────────────


@router.get("/providers", response_model=ProviderListOut)
async def get_provider_health(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    orchestrator: LLMOrchestrator = Depends(get_orchestrator),
):
    """Get health metrics for all configured LLM providers.

    Shows status (healthy/degraded/cooldown/disabled), health scores,
    error counts, and cooldown information.
    """
    return await orchestrator.get_provider_health(db, user["sub"])


@router.get("/models", response_model=ModelListOut)
async def get_model_health(
    provider: str | None = Query(None, description="Filter by provider"),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    orchestrator: LLMOrchestrator = Depends(get_orchestrator),
):
    """Get health metrics for all models, optionally filtered by provider.

    Shows state (READY/BUSY/COOLDOWN/DISABLED), average latency,
    success rates, and error information.
    """
    return await orchestrator.get_model_health(db, user["sub"], provider)
