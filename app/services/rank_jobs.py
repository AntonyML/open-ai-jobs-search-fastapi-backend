"""Ranking job coordinator — backed by the persistent ExecutionQueue.

Fase 6: POST /rank/ now validates no duplicate active job (partial unique
index), supports Idempotency-Key, snapshots profile + algorithm version,
and returns {job_id, status, total_jobs, accepted_jobs}.
"""

from __future__ import annotations


from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import CandidateProfile, ExecutionJob, ExecutionJobItem, IngestedJob, JobPosting
from app.services.orchestrator.execution_queue import ExecutionQueue
from app.services.orchestrator.orchestrator_deps import get_orchestrator
from app.services.rank import ALGORITHM_VERSION, PROMPT_VERSION

from app.core.logging import get_logger, bind_context
logger = get_logger(__name__)

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
    with bind_context(stage="rank"):
        queue = _get_queue()
        re_rank = payload.get("re_rank", False)

        # ── All DB operations in a single session (7→1 consolidation) ──
        async with db_factory() as db:
            # 1. Idempotency check (early return if hit)
            if idempotency_key:
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
                        "total_jobs": 0,
                        "accepted_jobs": 0,
                        "message": "Idempotency hit — job already exists.",
                    }

            # 2. Load candidate profile + count unranked jobs
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

            job_ids = payload.get("job_ids")
            if not job_ids:
                # Ranking now requires an explicit job selection from the
                # search step. The old fallback that imported the entire
                # shared ingested_jobs pool and enqueued one LLM call per
                # job was removed — it burned credits on unfiltered bulk runs.
                return {
                    "job_id": None,
                    "status": "skipped",
                    "total_jobs": 0,
                    "accepted_jobs": 0,
                    "message": "Select jobs to rank first.",
                }

            ingested_result = await db.execute(
                select(IngestedJob).where(IngestedJob.id.in_(job_ids))
            )
            ingested_list = list(ingested_result.scalars().all())
            if not ingested_list:
                return {
                    "job_id": None,
                    "status": "skipped",
                    "total_jobs": 0,
                    "accepted_jobs": 0,
                    "message": "No ingested jobs found for given IDs.",
                }
            jobs = []
            for ij in ingested_list:
                existing = await db.get(JobPosting, ij.id)
                if existing is None:
                    jp = JobPosting(
                        id=ij.id,
                        user_id=user_id,
                        portal=ij.portal or "web",
                        external_id=f"ij_{ij.id}",
                        title=ij.title,
                        company=ij.company,
                        location=ij.location,
                        url=ij.url,
                        description=ij.description,
                        salary=ij.salary,
                        status="new",
                    )
                    db.add(jp)
                    jobs.append(jp)
                else:
                    jobs.append(existing)
            await db.flush()
            total_jobs = len(jobs)
            if total_jobs == 0:
                return {
                    "job_id": None,
                    "status": "skipped",
                    "total_jobs": 0,
                    "accepted_jobs": 0,
                    "message": "No unranked jobs found.",
                }

            # 3. Verify no other active rank job exists
            existing_active = await db.execute(
                select(ExecutionJob).where(
                    ExecutionJob.user_id == user_id,
                    ExecutionJob.pipeline == "rank",
                    ExecutionJob.status.in_(["queued", "running"]),
                )
            )
            active_job = existing_active.scalar_one_or_none()
            if active_job is not None:
                logger.info(
                    "Active rank job %s already exists for user %s — returning it",
                    active_job.id, user_id,
                )
                return {
                    "job_id": active_job.id,
                    "status": active_job.status,
                    "total_jobs": total_jobs,
                    "accepted_jobs": 0,
                    "message": f"Rank run already in progress (job {active_job.id}).",
                }

            # 4. Enqueue (creates ExecutionJob + commits internally)
            try:
                job_id, execution_job = await queue.enqueue(
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
            except IntegrityError:
                # Race condition: another request snuck in an active job
                await db.rollback()
                recovered = await db.execute(
                    select(ExecutionJob).where(
                        ExecutionJob.user_id == user_id,
                        ExecutionJob.pipeline == "rank",
                        ExecutionJob.status.in_(["queued", "running"]),
                    )
                )
                active_job = recovered.scalar_one_or_none()
                if active_job is not None:
                    logger.info(
                        "Deduplicated concurrent rank job for user %s — returning existing %s",
                        user_id, active_job.id,
                    )
                    return {
                        "job_id": active_job.id,
                        "status": active_job.status,
                        "total_jobs": total_jobs,
                        "accepted_jobs": 0,
                        "message": f"Rank run already in progress (job {active_job.id}).",
                    }
                raise

            # 5. Set idempotency key + create items in a single new transaction
            #    (enqueue already committed, so autobegin starts a new transaction)
            if idempotency_key:
                db_job = await db.get(ExecutionJob, job_id)
                if db_job is not None:
                    db_job.idempotency_key = idempotency_key

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

            # 6. pg_notify (same session, best-effort)
            try:
                await db.execute(text("SELECT pg_notify('job_queued', '')"))
                await db.commit()
            except Exception:
                logger.debug("pg_notify failed (non-critical)")

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
