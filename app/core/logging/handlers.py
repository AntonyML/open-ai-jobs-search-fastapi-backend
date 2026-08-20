"""Handler factories — build console + file handlers with proper filtering."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.core.logging.config import LoggingConfig
from app.core.logging.filters import DomainFilter, HealthCheckFilter, PIIFilter
from app.core.logging.formatters import ConsoleFormatter, FileFormatter, JsonFormatter


def build_console_handler(config: LoggingConfig) -> logging.StreamHandler:
    """Terminal handler. Dev: INFO+ with color. Prod: WARNING+."""
    handler = logging.StreamHandler()
    level = config.console_level if config.is_development else "WARNING"
    handler.setLevel(getattr(logging, level.upper()))
    handler.setFormatter(ConsoleFormatter(colorize=config.console_colorize))
    handler.addFilter(PIIFilter())
    return handler


def build_file_handler(
    config: LoggingConfig,
    filename: str,
    level: str = "INFO",
    backup_count: int | None = None,
    domain: str | None = None,
) -> RotatingFileHandler:
    """Rotating file handler for a specific log file."""
    config.log_dir.mkdir(parents=True, exist_ok=True)
    filepath = config.log_dir / filename

    handler = RotatingFileHandler(
        filepath,
        maxBytes=config.max_bytes,
        backupCount=backup_count or config.backup_count,
        encoding="utf-8",
    )
    handler.setLevel(getattr(logging, level.upper()))
    handler.setFormatter(JsonFormatter() if config.is_production else FileFormatter())
    handler.addFilter(PIIFilter())
    if domain:
        handler.addFilter(DomainFilter(domain))
    return handler


def build_all_handlers(config: LoggingConfig) -> list[logging.Handler]:
    """Build every handler according to the config."""
    handlers: list[logging.Handler] = []

    # 1. Console (terminal)
    handlers.append(build_console_handler(config))

    # 2. app.log — everything INFO+
    handlers.append(build_file_handler(config, "app.log", "INFO"))

    # 3. error.log — only ERROR+
    handlers.append(
        build_file_handler(
            config,
            "error.log",
            "ERROR",
            backup_count=config.error_backup_count,
        )
    )

    # 4. Domain-specific logs
    for domain, filename in config.domain_logs.items():
        handlers.append(
            build_file_handler(
                config,
                filename,
                "DEBUG",
                domain=domain,
            )
        )

    # 5. access.log with healthcheck filter
    access_handler = build_file_handler(config, "access.log", "INFO", domain="access")
    access_handler.addFilter(HealthCheckFilter())
    handlers.append(access_handler)

    return handlers
