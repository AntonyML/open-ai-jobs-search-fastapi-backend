"""Orchestrator router — endpoints for managing the LLM execution queue.

Exposes monitoring and control endpoints:
- Queue status, pause, resume, cancel, retry
- Provider health metrics
- Model health metrics
- Individual job status
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.security import decode_access_token
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
from app.services.orchestrator.queue_notifier import get_queue_notifier

logger = logging.getLogger(__name__)

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


# ── WebSocket for real-time queue updates ───────────────────────────


@router.websocket("/ws")
async def queue_websocket(ws: WebSocket):
    """WebSocket endpoint for real-time queue status updates.

    Authentication: pass JWT token as query parameter (?token=...).
    Once connected, the server pushes QueueStatus JSON whenever the
    execution queue state changes.

    The client does NOT need to send any messages — this is a one-way
    stream of status updates.
    """
    # ── Authenticate via token query param ────────────────────────
    token = ws.query_params.get("token", "")
    if not token:
        await ws.close(code=4001, reason="Missing token")
        return

    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            await ws.close(code=4001, reason="Invalid token")
            return
    except Exception:
        await ws.close(code=4001, reason="Invalid or expired token")
        return

    await ws.accept()

    # ── Listen for queue changes and push updates ─────────────────
    notifier = get_queue_notifier()
    from app.db.session import async_session_factory

    try:
        # Send initial state immediately
        async with async_session_factory() as db:
            orchestrator = get_orchestrator()
            status = await orchestrator.get_queue_status(db, user_id)
            await ws.send_text(status.model_dump_json())

        # Then wait for changes and push updates
        while True:
            await notifier.wait_for_change()

            # Re-fetch and send the full status
            async with async_session_factory() as db:
                orchestrator = get_orchestrator()
                try:
                    status = await orchestrator.get_queue_status(db, user_id)
                    await ws.send_text(status.model_dump_json())
                except Exception as exc:
                    logger.warning("WS queue fetch failed: %s", exc)
                    continue

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for user %s", user_id)
    except asyncio.CancelledError:
        logger.debug("WebSocket handler cancelled (server shutdown) for user %s", user_id)
    except Exception as exc:
        logger.warning("WebSocket error for user %s: %s", user_id, exc)
