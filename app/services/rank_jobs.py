"""In-process ranking job coordinator — now backed by the persistent ExecutionQueue.

REFACTORED: Uses the LLMOrchestrator's ExecutionQueue for persistence,
checkpointing, and lifecycle management. The old in-memory dict is removed.

The queue survives backend restarts — unfinished jobs are resumed automatically.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rank import execute_rank
from app.services.orchestrator.execution_queue import ExecutionQueue
from app.services.orchestrator.orchestrator_deps import get_orchestrator
from app.db.models import ExecutionJob
from app.core.task_manager import background_tasks

logger = logging.getLogger(__name__)

# ── Queue instance (shared with orchestrator) ───────────────────────

def _get_queue() -> ExecutionQueue:
    """Get the shared ExecutionQueue from the orchestrator."""
    return get_orchestrator().queue


async def start(
    db_factory,
    user_id: str,
    payload: dict[str, Any],
) -> str:
    """Start a ranking run as an orchestrated background job.

    Uses the persistent ExecutionQueue instead of the old in-memory dict.
    The job survives restarts and is resumed automatically.

    Args:
        db_factory: AsyncSession factory (callable returning async context manager).
        user_id: The authenticated user's ID.
        payload: Rank request parameters (focus_area, re_rank, top_n).

    Returns:
        The execution job ID for status polling.
    """
    queue = _get_queue()

    async with db_factory() as db:
        job_id, _ = await queue.enqueue(
            db=db,
            user_id=user_id,
            pipeline="rank",
            description=f"Rank run: focus={payload.get('focus_area', 'all')}, re_rank={payload.get('re_rank', False)}",
            group_id=None,
            messages=None,
            output_schema="RankResult",
            max_retries=1,
            checkpoint_data=payload,
        )
        await db.commit()

    # Start the background execution
    background_tasks.create_task(
        _execute_rank_job(job_id, db_factory, user_id, payload),
        name=f"rank_{job_id[:8]}",
    )

    return job_id


async def _execute_rank_job(
    job_id: str,
    db_factory,
    user_id: str,
    payload: dict[str, Any],
) -> None:
    """Execute the ranking job in the background.

    This runs as an asyncio task, using the orchestrator's queue for lifecycle.
    """
    queue = _get_queue()

    async with db_factory() as db:
        try:
            # Start the job
            job = await queue.start_job(db, job_id, provider="orchestrator", model="internal")

            if job is None:
                logger.warning("Rank job %s not found in queue", job_id)
                return

            start_time = datetime.now(timezone.utc)

            # Execute the rank logic
            result = await execute_rank(db, user_id, **payload)

            execution_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

            # Complete the job
            await queue.complete_job(
                db=db,
                job_id=job_id,
                result_data=result.model_dump() if hasattr(result, "model_dump") else {"ranked_count": result.ranked_count},
                execution_time_ms=execution_time,
            )
            await db.commit()

            logger.info(
                "Rank job %s completed | %d jobs in %dms",
                job_id, result.ranked_count, execution_time,
            )

        except asyncio.CancelledError:
            await queue.cancel_job(db, job_id)
            await db.commit()
            logger.info("Rank job %s cancelled", job_id)

        except Exception as exc:
            await queue.fail_job(db, job_id, str(exc), "server_error", should_retry=False)
            await db.commit()
            logger.exception("Rank job %s failed: %s", job_id, exc)


async def get(job_id: str) -> dict[str, Any] | None:
    """Get the status of a ranking job from the execution queue.

    Args:
        job_id: The execution job ID.

    Returns:
        Dict with status info, or None if not found.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        job = await _get_queue().get_job(db, job_id)
        if job is None:
            return None

        return {
            "id": job.id,
            "status": job.status,
            "pipeline": job.pipeline,
            "description": job.description,
            "provider": job.provider,
            "model": job.model,
            "retry_count": job.retry_count,
            "last_error": job.last_error,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "execution_time_ms": job.execution_time_ms,
            "result": job.result,
        }


async def cancel(job_id: str) -> bool:
    """Cancel a ranking job.

    Args:
        job_id: The execution job ID.

    Returns:
        True if cancelled, False if not found or already completed.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        return await _get_queue().cancel_job(db, job_id)


async def get_queue_status(user_id: str) -> dict[str, Any] | None:
    """Get the overall queue status for the user's rank jobs.

    Returns:
        Queue status dict with pending, running, and completed job counts.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        status = await _get_queue().get_queue_status(db, user_id)
        return status
