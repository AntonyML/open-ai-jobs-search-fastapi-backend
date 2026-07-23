"""Ranking worker — separate process that claims & processes ExecutionJobItems.

Architecture
-----------
The worker runs as a standalone asyncio loop in its own process (Fly.io process
``worker``). It never shares a database session with the API process.

Lifecycle for each item:
  1. CLAIM  — ``SELECT ... FOR UPDATE SKIP LOCKED`` → status='running',
              locked_until=NOW+5min → commit → CLOSE session
  2. LOAD   — NEW session → fetch candidate + job + provider config → CLOSE
  3. RANK   — Call LLM (NO database connection held)
  4. SAVE   — NEW session → upsert RankEvaluation + insert RankEvaluationVersion
              + mark item completed → commit → CLOSE

Heartbeat
---------
A background task periodically extends ``locked_until`` for items this worker
is processing, preventing other workers from stealing them.

Recovery
--------
A separate background task resets items whose ``locked_until`` has expired
back to ``queued`` so they can be picked up again (worker crash / restart).

Shutdown
--------
On SIGTERM/SIGINT the worker stops claiming new items, waits for the current
item to finish (with timeout), and marks it ``queued`` if it didn't complete.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time as time_module
from datetime import datetime, timedelta, timezone
from typing import Any

import litellm
from sqlalchemy import select, text, update, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import get_settings
from app.db.models import (
    ExecutionJob,
    ExecutionJobItem,
    JobPosting,
    RankEvaluation,
    RankEvaluationVersion,
)
from app.db.models import CandidateProfile
from app.llm.adapter import _build_kwargs
from app.schemas.rank import RankQualitativeOutput
from app.services.rank import (
    PROMPT_VERSION,
    _build_rank_evaluation,
    _rank_single_job,
)
from app.services.provider_credentials import get_user_active_provider_config

logger = logging.getLogger("worker")

# ── Constants ────────────────────────────────────────────────────────

POLL_INTERVAL = 2.0        # seconds between claim attempts (fallback when LISTEN unavailable)
HEARTBEAT_INTERVAL = 30.0  # seconds between heartbeat updates
LEASE_DURATION = 300       # seconds (5 min) before another worker can steal
RECOVERY_INTERVAL = 60.0   # seconds between recovery cycles
SHUTDOWN_TIMEOUT = 30.0    # max seconds to wait for current item to finish
MAX_RETRIES = 3            # max attempts per item before marking failed

POLL_FALLBACK = 60.0       # fallback poll interval when LISTEN is unavailable

ALGORITHM_VERSION = "2.0.0"
WORKER_ID: str = ""


# ── Engine & session factory (small pool — worker never holds long sessions) ──

def _create_worker_engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=settings.app_env == "development",
    )


_worker_engine = _create_worker_engine()
_worker_session_factory = async_sessionmaker(
    _worker_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Database helpers ──────────────────────────────────────────────────

async def _claim_item(session: AsyncSession, worker_id: str) -> ExecutionJobItem | None:
    """Atomically claim one queued item via FOR UPDATE SKIP LOCKED."""
    result = await session.execute(
        select(ExecutionJobItem)
        .where(ExecutionJobItem.status == "queued")
        .where(
            (ExecutionJobItem.locked_until.is_(None))
            | (ExecutionJobItem.locked_until < datetime.now(timezone.utc))
        )
        .order_by(ExecutionJobItem.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    item = result.scalar_one_or_none()
    if item is None:
        return None

    now = datetime.now(timezone.utc)
    item.status = "running"
    item.worker_id = worker_id
    item.locked_until = now + timedelta(seconds=LEASE_DURATION)
    item.started_at = now
    item.attempt_count = (item.attempt_count or 0) + 1
    await session.commit()
    return item


async def _load_item_data(
    session: AsyncSession, item: ExecutionJobItem
) -> tuple[CandidateProfile, JobPosting, dict[str, Any], RankEvaluation | None]:
    """Load candidate, job, provider config, and existing evaluation."""
    result = await session.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == item.user_id)
    )
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise ValueError(f"Candidate profile not found for user {item.user_id}")

    if not candidate.full_name or not candidate.experience:
        raise ValueError(f"Profile incomplete for user {item.user_id}")

    job_result = await session.execute(
        select(JobPosting).where(JobPosting.id == item.job_posting_id)
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise ValueError(f"Job posting {item.job_posting_id} not found")

    provider_config = await get_user_active_provider_config(session, item.user_id)

    eval_result = await session.execute(
        select(RankEvaluation).where(
            RankEvaluation.user_id == item.user_id,
            RankEvaluation.job_posting_id == item.job_posting_id,
        )
    )
    existing_eval = eval_result.scalar_one_or_none()

    return candidate, job, provider_config, existing_eval


async def _save_result(
    session: AsyncSession,
    item: ExecutionJobItem,
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
    llm_output: RankQualitativeOutput | None,
    provider: str | None,
    model: str | None,
    latency_ms: int,
):
    """Persist evaluation result + version snapshot + mark item completed."""
    version = RankEvaluationVersion(
        evaluation_id=evaluation.id,
        user_id=item.user_id,
        job_posting_id=item.job_posting_id,
        overall_score=evaluation.overall_score,
        verdict=evaluation.verdict,
        profile_snapshot={
            "skills": candidate.skills,
            "experience": candidate.experience,
            "location": candidate.location,
            "constraints": candidate.constraints,
        },
        algorithm_version=ALGORITHM_VERSION,
        prompt_version=PROMPT_VERSION,
        model_provider=provider,
        model_name=model,
        input_hash="",
        token_usage={},
        latency_ms=latency_ms,
    )
    session.add(version)

    job.status = "ranked"
    job.rank_score = evaluation.overall_score
    job.rank_verdict = evaluation.verdict
    job.rank_date = datetime.now(timezone.utc)

    item.status = "completed"
    item.finished_at = datetime.now(timezone.utc)
    item.locked_until = None

    await _maybe_complete_job(session, item.execution_job_id)

    await session.commit()


async def _maybe_complete_job(session: AsyncSession, execution_job_id: str):
    """Mark the ExecutionJob complete if all its items are terminal."""
    count_result = await session.execute(
        select(sa_func.count()).select_from(ExecutionJobItem)
        .where(ExecutionJobItem.execution_job_id == execution_job_id)
    )
    total = count_result.scalar() or 0

    done_result = await session.execute(
        select(sa_func.count()).select_from(ExecutionJobItem)
        .where(
            ExecutionJobItem.execution_job_id == execution_job_id,
            ExecutionJobItem.status.in_(["completed", "failed"]),
        )
    )
    done = done_result.scalar() or 0

    if total > 0 and done >= total:
        result = await session.execute(
            select(ExecutionJob)
            .where(
                ExecutionJob.id == execution_job_id,
                ExecutionJob.status == "running",
            )
        )
        job = result.scalar_one_or_none()
        if job is not None:
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc)


async def _save_failure(
    session: AsyncSession,
    item: ExecutionJobItem,
    error: str,
    error_code: str,
):
    """Mark item as failed after exhausting retries."""
    item.status = "failed"
    item.last_error = error[:500]
    item.last_error_code = error_code
    item.finished_at = datetime.now(timezone.utc)
    item.locked_until = None
    await _maybe_complete_job(session, item.execution_job_id)
    await session.commit()


async def _recover_expired_leases(session: AsyncSession):
    """Reset items with expired locked_until back to queued."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(ExecutionJobItem)
        .where(ExecutionJobItem.status == "running")
        .where(ExecutionJobItem.locked_until < now)
        .with_for_update(skip_locked=True)
    )
    items = list(result.scalars().all())
    for item in items:
        item.status = "queued"
        item.worker_id = None
        item.locked_until = None
        logger.warning("Recovered expired item %s (worker %s)", item.id, item.worker_id)
    if items:
        await session.commit()


async def _heartbeat(session: AsyncSession, worker_id: str):
    """Extend locked_until for all items this worker is processing."""
    now = datetime.now(timezone.utc)
    new_deadline = now + timedelta(seconds=LEASE_DURATION)
    result = await session.execute(
        update(ExecutionJobItem)
        .where(
            ExecutionJobItem.worker_id == worker_id,
            ExecutionJobItem.status == "running",
        )
        .values(
            locked_until=new_deadline,
            heartbeat_at=now,
        )
    )
    if result.rowcount:
        await session.commit()


# ── LLM call ──────────────────────────────────────────────────────────

async def _call_llm(
    messages: list[dict[str, str]],
    output_schema: type,
    provider: str,
    model: str,
    api_key: str | None,
    api_base: str | None,
    temperature: float = 0.3,
    max_tokens: int = 1536,
) -> str:
    """Single LLM call — no session held."""
    kwargs = _build_kwargs(provider, model, api_key, api_base)
    kwargs["temperature"] = temperature
    kwargs["max_tokens"] = max_tokens
    kwargs["timeout"] = 30

    if output_schema is not None:
        schema_json = output_schema.model_json_schema()
        kwargs["response_format"] = {
            "type": "json_object",
            "response_schema": {
                "name": output_schema.__name__,
                "schema": schema_json,
                "strict": True,
            },
        }

    response = await litellm.acompletion(messages=messages, **kwargs)
    message = response.choices[0].message
    content = message.content or getattr(message, "reasoning_content", None)
    if content is None:
        raise ValueError("LLM returned empty response")
    return content


# ── Main processing ──────────────────────────────────────────────────

async def _process_item(item: ExecutionJobItem) -> None:
    """Process a single ExecutionJobItem end-to-end with short sessions."""
    # Phase 1: Load data (short session)
    async with _worker_session_factory() as db:
        candidate, job, provider_config, existing_eval = await _load_item_data(db, item)

    provider = provider_config.get("provider")
    model = provider_config.get("model")

    if not provider or not model:
        raise ValueError(f"User {item.user_id} has no active LLM provider configured")

    # Phase 2: Rank (pure computation + LLM via shared _rank_single_job)
    async def _worker_llm(messages, output_schema, _provider_config):
        api_key = _provider_config.get("api_key")
        api_base = _provider_config.get("api_base")
        start = time_module.monotonic()
        raw = await _call_llm(
            messages=messages,
            output_schema=output_schema,
            provider=_provider_config["provider"],
            model=_provider_config["model"],
            api_key=api_key,
            api_base=api_base,
        )
        return raw

    start = time_module.monotonic()
    ev_data = await _rank_single_job(
        candidate=candidate,
        job=job,
        provider_config=provider_config,
        user_id=item.user_id,
        existing_evaluation=existing_eval,
        llm_call_override=_worker_llm,
    )
    latency_ms = int((time_module.monotonic() - start) * 1000)

    # Phase 3: Save result (short session)
    async with _worker_session_factory() as db:
        c = await db.merge(candidate)
        j = await db.merge(job)
        evaluation = await _build_rank_evaluation(
            db=db, candidate=c, job=j,
            user_id=item.user_id,
            quantitative=ev_data["quantitative"],
            llm_output=ev_data["llm_output"],
            provider_config=provider_config,
            existing_evaluation=ev_data["existing_evaluation"],
            technical_score=ev_data["technical_score"],
            experience_score=ev_data["experience_score"],
            behavioral_score=ev_data["behavioral_score"],
            career_score=ev_data["career_score"],
            overall=ev_data["overall"],
            verdict=ev_data["verdict"],
            location_status=ev_data["location_status"],
            deadline=ev_data.get("deadline"),
            deadline_urgent=ev_data["deadline_urgent"],
            strengths=ev_data.get("strengths"),
            gaps=ev_data.get("gaps"),
            missing_keywords=ev_data.get("missing_keywords"),
            red_flags=ev_data.get("red_flags"),
            language=ev_data.get("language") or j.language,
            technical_fit=ev_data.get("quantitative", {}).get("technical_fit"),
            relevant_experience=ev_data.get("quantitative", {}).get("relevant_experience"),
            constraints_fit=ev_data.get("quantitative", {}).get("constraints_fit"),
            career_alignment=ev_data.get("quantitative", {}).get("career_alignment"),
            behavioral_fit=ev_data.get("quantitative", {}).get("behavioral_fit"),
        )
        await _save_result(
            db, item, c, j, evaluation, ev_data["llm_output"],
            provider, model, latency_ms,
        )

    logger.info(
        "Item %s completed | job=%s score=%d verdict=%s latency=%dms",
        item.id, job.title, ev_data["overall"], ev_data["verdict"], latency_ms,
    )


# ── Worker loop ───────────────────────────────────────────────────────

_shutdown_event = asyncio.Event()
_current_item: ExecutionJobItem | None = None


def _handle_signal(sig):
    logger.info("Received signal %s, initiating graceful shutdown...", sig)
    _shutdown_event.set()


async def _heartbeat_loop(worker_id: str):
    """Periodically extend leases for this worker's items."""
    while not _shutdown_event.is_set():
        try:
            async with _worker_session_factory() as db:
                await _heartbeat(db, worker_id)
        except Exception as exc:
            logger.warning("Heartbeat failed: %s", exc)
        try:
            await asyncio.wait_for(
                asyncio.sleep(HEARTBEAT_INTERVAL),
                timeout=HEARTBEAT_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass


async def _recovery_loop():
    """Periodically recover expired leases."""
    while not _shutdown_event.is_set():
        try:
            async with _worker_session_factory() as db:
                await _recover_expired_leases(db)
        except Exception as exc:
            logger.warning("Recovery failed: %s", exc)
        try:
            await asyncio.wait_for(
                asyncio.sleep(RECOVERY_INTERVAL),
                timeout=RECOVERY_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass


async def _listen_for_notifications(notify_event: asyncio.Event) -> None:
    """Connect via asyncpg and LISTEN for job_queued notifications."""
    try:
        import asyncpg
        from urllib.parse import urlparse

        settings = get_settings()
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        conn = await asyncpg.connect(
            user=url.username,
            password=url.password or "",
            host=url.hostname or "localhost",
            port=url.port or 5432,
            database=url.path.lstrip("/"),
        )
        await conn.add_listener("job_queued", lambda *_: notify_event.set())
        logger.info("LISTEN job_queued — waiting for notifications")
        await asyncio.Event().wait()  # run until cancelled
    except Exception as exc:
        logger.warning("LISTEN failed (%s), will fall back to polling", exc)


async def _worker_main():
    """Main worker loop — claim items and process them."""
    global _current_item

    logger.info("Worker %s started", WORKER_ID)

    # Start background tasks
    heartbeat_task = asyncio.create_task(_heartbeat_loop(WORKER_ID), name="heartbeat")
    recovery_task = asyncio.create_task(_recovery_loop(), name="recovery")

    # Listen for PostgreSQL notifications (wake on new items)
    notify_event = asyncio.Event()
    listen_task = asyncio.create_task(_listen_for_notifications(notify_event), name="listener")

    while not _shutdown_event.is_set():
        item: ExecutionJobItem | None = None
        try:
            # Wait for notification or fallback poll
            notify_event.clear()
            done, _ = await asyncio.wait(
                [notify_event.wait(), asyncio.sleep(POLL_FALLBACK)],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if _shutdown_event.is_set():
                break
            # Claim
            async with _worker_session_factory() as db:
                item = await _claim_item(db, WORKER_ID)

            if item is None:
                continue

            _current_item = item
            logger.info("Claimed item %s | job_posting=%s", item.id, item.job_posting_id)

            # Process
            await _process_item(item)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Failed to process item %s", item.id if item else "unknown")
            if item is not None:
                try:
                    async with _worker_session_factory() as db:
                        if (item.attempt_count or 0) >= MAX_RETRIES:
                            await _save_failure(db, item, str(exc), "server_error")
                        else:
                            item.status = "queued"
                            item.worker_id = None
                            item.locked_until = None
                            await db.commit()
                            logger.info("Item %s returned to queue (retry %d/%d)", item.id, item.attempt_count, MAX_RETRIES)
                except Exception as save_err:
                    logger.error("Failed to update item %s after error: %s", item.id, save_err)
        finally:
            _current_item = None

    # Shutdown
    heartbeat_task.cancel()
    recovery_task.cancel()
    listen_task.cancel()
    await asyncio.gather(heartbeat_task, recovery_task, listen_task, return_exceptions=True)

    if _current_item is not None:
        try:
            async with _worker_session_factory() as db:
                _current_item.status = "queued"
                _current_item.worker_id = None
                _current_item.locked_until = None
                await db.commit()
                logger.info("Released item %s back to queue during shutdown", _current_item.id)
        except Exception as exc:
            logger.error("Failed to release item during shutdown: %s", exc)

    await _worker_engine.dispose()
    logger.info("Worker %s shutdown complete", WORKER_ID)


def main():
    """Entry point — ``python -m app.worker``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    global WORKER_ID
    import uuid
    WORKER_ID = str(uuid.uuid4())[:8]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        loop.run_until_complete(_worker_main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
