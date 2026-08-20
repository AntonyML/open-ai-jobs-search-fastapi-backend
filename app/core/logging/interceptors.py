"""Interceptors for SQLAlchemy queries, httpx, and LiteLLM calls."""

from __future__ import annotations

import logging
import time

sql_logger = logging.getLogger("app.sql")
llm_logger = logging.getLogger("app.llm")


# ─── SQLAlchemy ──────────────────────────────────────────────────────────


def setup_sqlalchemy_logging(engine) -> None:
    """Log SQL queries via app.sql logger instead of echo=True.

    Slow queries (>100ms) are always logged. All queries logged in DEBUG.
    """
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("query_start_time", []).append(time.perf_counter())

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        start = conn.info["query_start_time"].pop()
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > 100 or sql_logger.isEnabledFor(logging.DEBUG):
            sql_logger.debug(
                "SQL (%.1fms): %s",
                elapsed,
                statement[:200],
                extra={"extra_data": {"latency_ms": round(elapsed, 1)}},
            )


# ─── LiteLLM / LLM calls ────────────────────────────────────────────────


class LLMCallLogger:
    """Context manager to log every LLM call with timing.

    Usage:
        llm_log = LLMCallLogger()
        async with llm_log.track(provider="anthropic", model="claude-sonnet-4", purpose="rank"):
            response = await litellm.acompletion(...)
    """

    def track(self, provider: str, model: str, purpose: str):
        return _LLMCallContext(provider, model, purpose)


class _LLMCallContext:
    def __init__(self, provider: str, model: str, purpose: str):
        self.provider = provider
        self.model = model
        self.purpose = purpose
        self._start = 0.0

    async def __aenter__(self):
        self._start = time.perf_counter()
        llm_logger.info(
            "LLM call → %s/%s [%s]",
            self.provider,
            self.model,
            self.purpose,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        elapsed = (time.perf_counter() - self._start) * 1000
        if exc_type:
            llm_logger.error(
                "LLM call FAILED ← %s/%s [%s] (%.0fms): %s",
                self.provider,
                self.model,
                self.purpose,
                elapsed,
                exc_val,
            )
        else:
            llm_logger.info(
                "LLM call OK ← %s/%s [%s] (%.0fms)",
                self.provider,
                self.model,
                self.purpose,
                elapsed,
                extra={
                    "extra_data": {
                        "provider": self.provider,
                        "model": self.model,
                        "purpose": self.purpose,
                        "latency_ms": round(elapsed),
                    }
                },
            )
        return False
