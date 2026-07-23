"""Ranking job coordinator — backed by the persistent ExecutionQueue.

Fase 6: POST /rank/ now validates no duplicate active job (partial unique
index), supports Idempotency-Key, snapshots profile + algorithm version,
and returns {job_id, status, total_jobs, accepted_jobs}.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.db.models import CandidateProfile, ExecutionJob, ExecutionJobItem, JobPosting
from app.services.orchestrator.execution_queue import ExecutionQueue
from app.services.orchestrator.orchestrator_deps import get_orchestrator
from app.services.rank import ALGORITHM_VERSION, PROMPT_VERSION

logger = logging.getLogger(__name__)

# ── Queue instance (shared with orchestrator) ───────────────────────


def _get_queue() -> ExecutionQueue:
    return get_orchestrator().queue


async def start(
    db_factory,
    user_id: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Start a ranking run and enqueue items for the worker.

    Flow (Fase 6):
      1. If ``idempotency_key`` provided and job already exists, return it.
      2. Verify no other active rank job exists for this user (partial
         unique index ``uq_active_rank_per_user`` on execution_jobs).
      3. Create ExecutionJob → status='queued'.
      4. Snapshot candidate profile + algorithm + prompt version.
      5. Select unranked jobs, create one ExecutionJobItem per job.
      6. Return ``{job_id, status, total_jobs, accepted_jobs}``.

    Args:
        db_factory: AsyncSession factory.
        user_id: The authenticated user's ID.
        payload: Rank request parameters (focus_area, re_rank, top_n).
        idempotency_key: Optional unique key for idempotent retries.

    Returns:
        Dict with job_id, status, total_jobs, accepted_jobs.
    """
    queue = _get_queue()
    re_rank = payload.get("re_rank", False)
    max_jobs = payload.get("max_jobs")

    # 1. Load candidate profile (for snapshot)
    async with db_factory() as db:
        cand_result = await db.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
        candidate = cand_result.scalar_one_or_none()
        profile_snapshot = {
            "skills": candidate.skills if candidate else {},
            "experience": candidate.experience if candidate else [],
            "location": candidate.location if candidate else None,
            "constraints": candidate.constraints if candidate else None,
            "job_target": candidate.job_target if candidate else {},
        }

        # 2. Select unranked jobs (count before creating items)
        query = select(JobPosting).where(JobPosting.user_id == user_id)
        if not re_rank:
            query = query.where(
                or_(
                    JobPosting.status == "new",
                    JobPosting.rank_score.is_(None),
                )
            )
        query = query.order_by(JobPosting.created_at.desc())
        if max_jobs is not None:
            query = query.limit(max_jobs)
        result = await db.execute(query)
        jobs = list(result.scalars().all())
        total_jobs = len(jobs)

    if total_jobs == 0:
        return {
            "job_id": None,
            "status": "skipped",
            "total_jobs": 0,
            "accepted_jobs": 0,
            "message": "No unranked jobs found.",
        }

    # 3. Idempotency check
    if idempotency_key:
        async with db_factory() as db:
            existing = await db.execute(
                select(ExecutionJob).where(
                    ExecutionJob.idempotency_key == idempotency_key
                )
            )
            existing_job = existing.scalar_one_or_none()
            if existing_job is not None:
                logger.info(
                    "Idempotency key %s hit — returning existing job %s",
                    idempotency_key, existing_job.id,
                )
                return {
                    "job_id": existing_job.id,
                    "status": existing_job.status,
                    "total_jobs": total_jobs,
                    "accepted_jobs": total_jobs,
                }

    # 4. Create ExecutionJob (status='queued')
    async with db_factory() as db:
        job_id, _ = await queue.enqueue(
            db=db,
            user_id=user_id,
            pipeline="rank",
            description=(
                f"Rank run: focus={payload.get('focus_area', 'all')}, "
                f"re_rank={re_rank}, jobs={total_jobs}"
            ),
            group_id=None,
            messages=None,
            output_schema="RankResult",
            max_retries=1,
            checkpoint_data={
                "payload": payload,
                "profile_snapshot": profile_snapshot,
                "algorithm_version": ALGORITHM_VERSION,
                "prompt_version": PROMPT_VERSION,
            },
        )

    # 4b. Set idempotency key (separate session — enqueue already committed)
    if idempotency_key:
        async with db_factory() as db:
            result = await db.execute(
                select(ExecutionJob).where(ExecutionJob.id == job_id)
            )
            db_job = result.scalar_one_or_none()
            if db_job is not None:
                db_job.idempotency_key = idempotency_key
            await db.commit()

    # 5. Create one item per job posting
    async with db_factory() as db:
        items = []
        for job in jobs:
            item = ExecutionJobItem(
                execution_job_id=job_id,
                job_posting_id=job.id,
                user_id=user_id,
                status="queued",
                locked_until=None,
            )
            db.add(item)
            items.append(item)

        await db.commit()

    logger.info(
        "Rank job %s enqueued | %d items for %d total jobs",
        job_id, len(items), total_jobs,
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "total_jobs": total_jobs,
        "accepted_jobs": len(items),
    }


async def get(job_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """Get the status of a ranking job from the execution queue.

    Args:
        job_id: The execution job ID.
        user_id: If provided, verifies the job belongs to this user.

    Returns:
        Dict with status info, or None if not found / not owned by user.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        from sqlalchemy import func as sa_func

        job = await _get_queue().get_job(db, job_id)
        if job is None:
            return None
        if user_id is not None and job.user_id != user_id:
            return None

        counts_result = await db.execute(
            select(
                sa_func.count().label("total"),
                sa_func.sum(sa_func.case((ExecutionJobItem.status == "completed", 1), else_=0)).label("completed"),
                sa_func.sum(sa_func.case((ExecutionJobItem.status == "running", 1), else_=0)).label("running"),
                sa_func.sum(sa_func.case((ExecutionJobItem.status == "failed", 1), else_=0)).label("failed"),
            ).where(ExecutionJobItem.execution_job_id == job_id)
        )
        counts = counts_result.one()

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
            "items": {
                "total": counts.total or 0,
                "completed": counts.completed or 0,
                "running": counts.running or 0,
                "failed": counts.failed or 0,
            },
        }


async def cancel(job_id: str, user_id: str | None = None) -> bool:
    """Cancel a ranking job.

    Args:
        job_id: The execution job ID.
        user_id: If provided, only cancels if the job belongs to this user.

    Returns:
        True if cancelled, False if not found, already completed, or not owned.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        job = await _get_queue().get_job(db, job_id)
        if job is None:
            return False
        if user_id is not None and job.user_id != user_id:
            return False

        return await _get_queue().cancel_job(db, job_id)


async def get_queue_status(user_id: str) -> dict[str, Any] | None:
    """Get the overall queue status for the user's rank jobs."""
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        return await _get_queue().get_queue_status(db, user_id)
