"""LLM Orchestrator — resilient multi-provider execution engine.

Every LLM call in the application MUST go through this orchestrator.
No service should call LiteLLM or llm_completion_structured directly.

Architecture decisions:
- **Failover priority**: Same provider → Same provider different model →
  Different provider same model → Next provider → Pause queue
- **429 handling**: Never blocks ranking. Reads Retry-After or exponential backoff.
  Marks model cooling down, automatically switches to next model.
- **Concurrency**: Configurable worker count, semaphore-based, respects provider limits.
- **Checkpoints**: Persists job progress. If backend restarts, continues from last
  unfinished job. Never restarts from zero.
"""
from __future__ import annotations

import json

import time
from datetime import datetime, timezone
from typing import Any, Callable

import litellm
from litellm import acompletion
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.session import async_session_factory
from app.exceptions import LLMError, ProviderAuthError
from app.schemas.orchestrator import (
    ExecutionJobOut,
    ModelHealthOut,
    ModelListOut,
    ProviderHealthOut,
    ProviderListOut,
    QueueControlRequest,
    QueueControlResult,
    QueueStatusOut,
)
from app.services.orchestrator import execution_queue as eq
from app.services.orchestrator import (
    llm_response_sanitizer as sanitizer,
    model_manager as mm,
    provider_manager as pm,
)

from app.core.logging import get_logger
logger = get_logger(__name__)


class LLMOrchestrator:
    """Central LLM execution orchestrator with automatic failover.

    Usage:
        orchestrator = LLMOrchestrator()
        result = await orchestrator.execute(
            user_id="...",
            messages=[...],
            output_schema=RankLLMOutput,
            pipeline="rank",
        )

    This is a singleton — create once at application startup.
    """

    def __init__(self, max_concurrency: int = 4):
        self.queue = eq.ExecutionQueue(max_concurrency=max_concurrency)
        self._settings = get_settings()

    async def execute(
        self,
        user_id: str,
        messages: list[dict[str, str]],
        output_schema: type | None = None,
        *,
        pipeline: str = "llm",
        description: str | None = None,
        group_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        max_retries: int = 3,
        field_constraints: dict[str, dict[str, int]] | None = None,
        checkpoint_data: dict | None = None,
    ) -> Any:
        """Execute an LLM call through the orchestrator.

        This is the main entry point for all LLM calls.
        Manages its own short-lived database sessions so no connection
        is held idle during LLM API calls (10–60s).

        Args:
            user_id: The authenticated user's ID.
            messages: Chat messages for the LLM.
            output_schema: Optional Pydantic model class for structured output.
            pipeline: Pipeline name (rank, apply, interview, expand, upskill).
            description: Human-readable description of this job.
            group_id: Group ID for batch jobs.
            provider: Preferred provider (None = auto-select from user config).
            model: Preferred model (None = auto-select).
            temperature: Sampling temperature.
            max_tokens: Max tokens in the response.
            max_retries: Max retry attempts.
            field_constraints: Optional array length constraints for sanitization.
            checkpoint_data: Optional checkpoint data for batch progress tracking.

        Returns:
            If output_schema is provided: validated Pydantic model instance.
            If output_schema is None: raw text response.

        Raises:
            LLMError: If all providers/models fail after exhausting retries.
            ProviderAuthError: If no valid provider configuration exists.
        """
        async with async_session_factory() as db:
            # Step 1: Get user's provider configuration
            provider_config = await self._resolve_provider_config(
                db, user_id, provider, model
            )

            # Step 2: Enqueue the job
            schema_name = output_schema.__name__ if output_schema else None
            job_id, job_model = await self.queue.enqueue(
                db=db,
                user_id=user_id,
                pipeline=pipeline,
                description=description,
                group_id=group_id,
                messages=messages,
                output_schema=schema_name,
                max_retries=max_retries,
                checkpoint_data=checkpoint_data,
            )

            logger.info(
                "Executing job %s | pipeline=%s provider=%s model=%s",
                job_id, pipeline, provider_config["provider"], provider_config["model"],
            )

            # Step 3: Build the execution plan immediately (while we have the session)
            execution_plan = await self._build_execution_plan(
                db, user_id, provider_config
            )

            # Commit the enqueued job so other short-lived sessions can see it
            await db.commit()

            logger.info(
                "Job %s plan built | %d attempts possible",
                job_id, len(execution_plan),
            )
        # Session returned to pool — LLM call phase holds no DB connection

        # Step 4: Execute with failover (uses its own short-lived sessions)
        result = await self._execute_with_failover(
            job_id=job_id,
            user_id=user_id,
            messages=messages,
            output_schema=output_schema,
            provider_config=provider_config,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            field_constraints=field_constraints,
            execution_plan=execution_plan,
        )

        return result

    async def _execute_with_failover(
        self,
        job_id: str,
        user_id: str,
        messages: list[dict[str, str]],
        output_schema: type | None,
        provider_config: dict[str, Any],
        temperature: float,
        max_tokens: int,
        max_retries: int,
        field_constraints: dict | None,
        execution_plan: list[tuple[str, str]],
    ) -> Any:
        """Execute with automatic failover across providers and models.

        Uses short-lived database sessions so no connection is held idle
        during LLM API calls (10–60s). Each DB operation acquires its own
        session and returns it to the pool immediately after commit.

        Failover priority:
        1. Same provider, same model (initial attempt)
        2. Same provider, different model (model fallback)
        3. Different provider, best available model (provider failover)
        4. Next tier provider
        5. If all fail, raise LLMError
        """
        attempted: set[tuple[str, str]] = set()
        last_error: Exception | None = None

        for attempt_tier, (prov, mdl) in enumerate(execution_plan, start=1):
            if (prov, mdl) in attempted:
                continue
            attempted.add((prov, mdl))

            # ── Phase 1: Start the job (short session) ──────────────
            async with async_session_factory() as session:
                job = await self.queue.start_job(
                    session, job_id, prov, mdl, attempt_tier
                )
                if job is None:
                    continue
                await session.commit()
            # Session returned to pool

            # ── Phase 2: LLM call (NO session held) ─────────────────
            try:
                start_time = time.monotonic()
                raw_response = await self._call_llm(
                    provider=prov,
                    model=mdl,
                    messages=messages,
                    output_schema=output_schema,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    api_key=provider_config.get("api_key"),
                    api_base=provider_config.get("api_base"),
                )
                latency_ms = int((time.monotonic() - start_time) * 1000)

                # Sanitize the response
                if output_schema is not None:
                    cleaned = sanitizer.sanitize_llm_response(
                        raw_response,
                        output_schema.__name__,
                        field_constraints or sanitizer.default_field_constraints(),
                    )
                    result = output_schema.model_validate(cleaned)
                else:
                    result = raw_response

                # ── Phase 3: Record success (short session) ─────────
                async with async_session_factory() as session:
                    await pm.record_success(session, user_id, prov, latency_ms)
                    await mm.mark_model_completed(
                        session, user_id, prov, mdl, latency_ms, success=True
                    )
                    await self.queue.complete_job(
                        session, job_id,
                        result_data=result.model_dump() if hasattr(result, "model_dump") else None,
                        execution_time_ms=latency_ms,
                    )
                    await session.commit()

                logger.info(
                    "Job %s success | provider=%s model=%s latency=%dms tier=%d",
                    job_id, prov, mdl, latency_ms, attempt_tier,
                )
                return result

            except (ProviderAuthError, LLMError, Exception) as exc:
                last_error = exc
                error_code = "server_error"

                if isinstance(exc, ProviderAuthError):
                    error_code = "auth_error"
                elif isinstance(exc, LLMError):
                    error_code = self._classify_error(str(exc))

                should_retry = (
                    error_code in ("auth_error", "rate_limit", "timeout")
                    or attempt_tier < len(execution_plan)
                )

                # ── Phase 4: Record failure (short session) ─────────
                async with async_session_factory() as session:
                    await pm.record_failure(
                        session, user_id, prov, error_code, str(exc)
                    )
                    await mm.mark_model_failed(
                        session, user_id, prov, mdl, error_code, str(exc)
                    )
                    if error_code == "rate_limit":
                        await self.queue.rate_limit_job(
                            session, job_id, cooldown_seconds=60
                        )
                    else:
                        await self.queue.fail_job(
                            session, job_id, str(exc), error_code,
                            should_retry=should_retry,
                        )
                    try:
                        await session.commit()
                    except Exception:
                        await session.rollback()

                logger.warning(
                    "Job %s %s | provider=%s model=%s → failover",
                    job_id, error_code, prov, mdl,
                )

        # All attempts exhausted
        async with async_session_factory() as session:
            await self.queue.fail_job(
                session, job_id,
                str(last_error) if last_error else "All providers/models exhausted",
                "exhausted",
                should_retry=False,
            )
            try:
                await session.commit()
            except Exception:
                await session.rollback()

        raise LLMError(
            f"Job {job_id}: All providers/models failed after {len(attempted)} attempts. "
            f"Last error: {last_error}"
        ) from last_error

    async def _resolve_provider_config(
        self,
        db: AsyncSession,
        user_id: str,
        preferred_provider: str | None,
        preferred_model: str | None,
    ) -> dict[str, Any]:
        """Resolve the provider configuration for a user.

        Uses the user's active provider if none specified, with fallback
        to the default provider from settings.
        """
        from app.services.provider_credentials import get_user_active_provider_config

        try:
            config = await get_user_active_provider_config(db, user_id)
        except Exception:
            # Fallback to settings defaults
            config = {
                "provider": self._settings.llm_default_provider,
                "model": "claude-sonnet-4-20250514",
                "api_key": None,
                "api_base": None,
            }

        if preferred_provider:
            config["provider"] = preferred_provider
        if preferred_model:
            config["model"] = preferred_model

        return config

    async def _build_execution_plan(
        self,
        db: AsyncSession,
        user_id: str,
        initial_config: dict[str, Any],
    ) -> list[tuple[str, str]]:
        """Build an ordered list of (provider, model) to try for failover.

        Priority:
        1. Initial provider + model
        2. Initial provider + alternative models
        3. Available providers (by priority) + their best model
        4. Available providers + alternative models
        """
        plan: list[tuple[str, str]] = []
        initial_provider = initial_config["provider"]
        initial_model = initial_config["model"]

        # 1. Initial provider + model
        plan.append((initial_provider, initial_model))

        # 2. Same provider, alternative models
        available_models = await mm.get_available_models(
            db, user_id, initial_provider
        )
        for m in available_models:
            if m.model_name != initial_model:
                plan.append((initial_provider, m.model_name))

        # 3. Available providers by priority
        available_providers = await pm.get_available_providers(db, user_id)
        for p in available_providers:
            if p.provider == initial_provider:
                continue  # Already covered above
            # Get best available model for this provider
            provider_models = await mm.get_available_models(
                db, user_id, p.provider
            )
            if provider_models:
                plan.append((p.provider, provider_models[0].model_name))
                # Alternative models
                for m in provider_models[1:]:
                    plan.append((p.provider, m.model_name))

        return plan

    async def _call_llm(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        output_schema: type | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> str:
        """Make the actual LiteLLM call.

        This is the only place in the application that calls LiteLLM directly.

        Args:
            provider: Provider name (e.g. "anthropic", "openai").
            model: Model name (without provider prefix).
            messages: Chat messages.
            output_schema: Optional Pydantic model for structured output.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.
            api_key: API key from user config (passed through from _resolve_provider_config).
            api_base: Base URL from user config.
        """
        from app.llm.adapter import _build_kwargs

        # Resolution order: 1) parameter passed from config, 2) settings env var
        if api_key is None:
            key_map = {
                "anthropic": self._settings.anthropic_api_key,
                "openai": self._settings.openai_api_key,
                "nvidia_nim": self._settings.nvidia_nim_api_key,
            }
            api_key = key_map.get(provider)

        if api_key is None and provider not in ("lm_studio", "ollama"):
            raise ProviderAuthError(
                f"No API key configured for provider '{provider}'. "
                f"Set it in .env or store it encrypted in your user profile."
            )

        if api_base is None and provider == "lm_studio":
            api_base = self._settings.lm_studio_api_base

        kwargs = _build_kwargs(provider, model, api_key, api_base)
        kwargs["temperature"] = temperature
        kwargs["max_tokens"] = max_tokens
        kwargs["timeout"] = 30
        kwargs["num_retries"] = 0  # We handle retries ourselves

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

        try:
            response = await acompletion(messages=messages, **kwargs)
            message = response.choices[0].message
            content = message.content or getattr(message, "reasoning_content", None)
            if content is None:
                raise LLMError("LLM returned empty response")
            return content

        except litellm.exceptions.AuthenticationError as exc:
            raise ProviderAuthError(str(exc)) from exc
        except litellm.exceptions.BadRequestError as exc:
            raise LLMError(f"LLM request error: {exc}") from exc
        except litellm.exceptions.RateLimitError as exc:
            raise LLMError(f"LLM rate-limited: {exc}") from exc
        except litellm.exceptions.Timeout as exc:
            raise LLMError(f"LLM timed out: {exc}") from exc
        except Exception as exc:
            raise LLMError(f"LLM call failed: {exc}") from exc

    def _classify_error(self, error_message: str) -> str:
        """Classify an error message into a standard error code."""
        error_lower = error_message.lower()
        if "rate" in error_lower and ("limit" in error_lower or "429" in error_lower):
            return "rate_limit"
        if "timeout" in error_lower or "timed out" in error_lower:
            return "timeout"
        if "auth" in error_lower or "401" in error_lower or "403" in error_lower:
            return "auth_error"
        if "502" in error_lower or "503" in error_lower or "504" in error_lower:
            return "server_error"
        if "empty" in error_lower:
            return "empty_response"
        return "server_error"

    # ── Queue management API ──────────────────────────────────────────

    async def get_queue_status(
        self, db: AsyncSession, user_id: str
    ) -> QueueStatusOut:
        """Get the current queue status for a user."""
        status = await self.queue.get_queue_status(db, user_id)

        return QueueStatusOut(
            paused=status["paused"],
            max_concurrency=status["max_concurrency"],
            active_workers=status["active_workers"],
            total_enqueued=status["total_enqueued"],
            total_completed=status["total_completed"],
            total_failed=status["total_failed"],
            total_cancelled=status["total_cancelled"],
            pending_jobs=[ExecutionJobOut.model_validate(j) for j in status["pending_jobs"]],
            running_jobs=[ExecutionJobOut.model_validate(j) for j in status["running_jobs"]],
            recent_completed=[ExecutionJobOut.model_validate(j) for j in status["recent_completed"]],
        )

    async def handle_queue_control(
        self,
        db: AsyncSession,
        user_id: str,
        action: str,
        job_id: str | None = None,
    ) -> QueueControlResult:
        """Handle queue control actions: pause, resume, cancel, retry_failed."""
        if action == "pause":
            await self.queue.pause_queue(db, user_id)
            return QueueControlResult(
                action="pause", affected_jobs=0, message="Queue paused"
            )

        elif action == "resume":
            count = await self.queue.resume_queue(db, user_id)
            return QueueControlResult(
                action="resume", affected_jobs=count,
                message=f"Queue resumed, {count} jobs moved to queued",
            )

        elif action == "cancel":
            if job_id:
                success = await self.queue.cancel_job(db, job_id)
                return QueueControlResult(
                    action="cancel", affected_jobs=1 if success else 0,
                    message="Job cancelled" if success else "Job not found or already completed",
                )
            # Cancel all non-terminal jobs (includes pending jobs when queue is paused)
            jobs = await self.queue.get_jobs_by_status(
                db, user_id, eq.ACTIVE_STATES | {eq.STATUS_PENDING}, limit=100
            )
            count = 0
            for j in jobs:
                if await self.queue.cancel_job(db, j.id):
                    count += 1
            await db.commit()
            return QueueControlResult(
                action="cancel", affected_jobs=count,
                message=f"Cancelled {count} active jobs",
            )

        elif action == "retry_failed":
            count = await self.queue.retry_failed_jobs(db, user_id, job_id)
            await db.commit()
            return QueueControlResult(
                action="retry_failed", affected_jobs=count,
                message=f"Retrying {count} failed jobs",
            )

        raise ValueError(f"Unknown action: {action}")

    # ── Provider health API ─────────────────────────────────────────

    async def get_provider_health(
        self, db: AsyncSession, user_id: str
    ) -> ProviderListOut:
        """Get health status for all providers."""
        providers = await pm.get_provider_health_status(db, user_id)
        return ProviderListOut(
            providers=[
                ProviderHealthOut(
                    provider=p.provider,
                    status=p.status,
                    priority=p.priority,
                    cooldown_until=p.cooldown_until,
                    total_calls=p.total_calls,
                    success_count=p.success_count,
                    failure_count=p.failure_count,
                    rate_limit_count=p.rate_limit_count,
                    timeout_count=p.timeout_count,
                    consecutive_failures=p.consecutive_failures,
                    health_score=p.health_score,
                    last_latency_ms=p.last_latency_ms,
                    last_error=p.last_error,
                    last_error_code=p.last_error_code,
                )
                for p in providers
            ]
        )

    async def get_model_health(
        self, db: AsyncSession, user_id: str, provider: str | None = None
    ) -> ModelListOut:
        """Get health status for all models, optionally filtered by provider."""
        models = await mm.get_model_health_status(db, user_id, provider)
        return ModelListOut(
            provider=provider or "all",
            models=[
                ModelHealthOut(
                    provider=m.provider,
                    model_name=m.model_name,
                    state=m.state,
                    priority=m.priority,
                    cost_rank=m.cost_rank,
                    context_window=m.context_window,
                    cooldown_until=m.cooldown_until,
                    average_latency_ms=m.average_latency_ms,
                    average_success_rate=m.average_success_rate,
                    total_calls=m.total_calls,
                    last_error=m.last_error,
                    last_error_code=m.last_error_code,
                )
                for m in models
            ]
        )

    async def get_job(
        self, db: AsyncSession, job_id: str, user_id: str | None = None
    ) -> ExecutionJobOut | None:
        """Get an execution job by ID, optionally scoped to a user."""
        job = await self.queue.get_job(db, job_id)
        if job is None:
            return None
        if user_id is not None and job.user_id != user_id:
            return None
        return ExecutionJobOut.model_validate(job)
