"""Sentry integration — production-only error tracking with context enrichment."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.logging.config import LoggingConfig


def init_sentry(config: LoggingConfig) -> None:
    """Initialize Sentry SDK with LoggingIntegration plus context enrichment."""
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    from app.core.logging.context import get_context

    def before_send(event, hint):
        ctx = get_context()
        event.setdefault("tags", {})
        if ctx.request_id:
            event["tags"]["request_id"] = ctx.request_id
        if ctx.stage:
            event["tags"]["stage"] = ctx.stage
        if ctx.provider:
            event["tags"]["llm_provider"] = ctx.provider
        if ctx.user_id:
            event.setdefault("user", {})["id"] = ctx.user_id
        return event

    sentry_sdk.init(
        dsn=config.sentry_dsn,
        environment=config.env.value,
        traces_sample_rate=config.sentry_traces_sample_rate,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        before_send=before_send,
    )
