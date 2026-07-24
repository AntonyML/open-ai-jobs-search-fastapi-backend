"""Logging system — call ``setup_logging()`` once at startup, then use ``get_logger()`` everywhere.

Usage in services:
    from app.core.logging import get_logger
    logger = get_logger(__name__)  # → "app.services.rank"
"""

from __future__ import annotations

import logging

from app.core.logging.config import LoggingConfig
from app.core.logging.handlers import build_all_handlers
from app.core.logging.context import bind_context, log_context

_config: LoggingConfig | None = None


def setup_logging(config: LoggingConfig | None = None) -> None:
    """Initialize logging ONCE at startup (call from create_app() and worker.py)."""
    global _config
    _config = config or LoggingConfig()

    root = logging.getLogger()
    root.setLevel(getattr(logging, _config.level.upper()))

    # Clear previous handlers (avoids duplicates on reload)
    root.handlers.clear()

    for handler in build_all_handlers(_config):
        root.addHandler(handler)

    # Suppress noisy libraries
    for logger_name, level in _config.suppressed_loggers.items():
        logging.getLogger(logger_name).setLevel(getattr(logging, level.upper()))

    # Sentry (production only)
    if _config.sentry_dsn and _config.is_production:
        from app.core.logging.sentry import init_sentry
        init_sentry(_config)

    logging.getLogger("app").info(
        "Logging initialized │ env=%s level=%s log_dir=%s",
        _config.env.value, _config.level, _config.log_dir,
    )


def get_logger(name: str) -> logging.Logger:
    """Get a hierarchical logger.

    Convention: ``get_logger(__name__)`` → ``"app.services.rank"``
    """
    return logging.getLogger(name)
