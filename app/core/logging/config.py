"""Logging configuration — driven by environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass(frozen=True)
class LoggingConfig:
    """Toda la configuración de logging, leída una sola vez al startup."""

    env: Environment = Environment(os.getenv("APP_ENV", "development"))
    level: str = os.getenv("LOG_LEVEL", "INFO")
    log_dir: Path = Path(os.getenv("LOG_DIR", "logs"))

    max_bytes: int = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    backup_count: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    error_backup_count: int = int(os.getenv("LOG_ERROR_BACKUP_COUNT", "10"))

    console_level: str = os.getenv("LOG_CONSOLE_LEVEL", "WARNING")
    console_colorize: bool = os.getenv("LOG_COLORIZE", "true").lower() == "true"

    domain_logs: dict[str, str] = field(default_factory=lambda: {
        "orchestrator": "orchestrator.log",
        "services.orchestrator": "orchestrator.log",
        "llm": "llm.log",
        "pipeline": "pipeline.log",
        "scraper": "scraper.log",
        "latex": "latex.log",
        "sql": "sql.log",
        "access": "access.log",
    })

    suppressed_loggers: dict[str, str] = field(default_factory=lambda: {
        "httpx": "WARNING",
        "httpcore": "WARNING",
        "asyncpg": "WARNING",
        "litellm": "WARNING",
        "uvicorn.access": "WARNING",
        "apscheduler": "WARNING",
        "hpack": "WARNING",
        "charset_normalizer": "WARNING",
    })

    sentry_dsn: str | None = os.getenv("SENTRY_DSN")
    sentry_traces_sample_rate: float = float(
        os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")
    )

    @property
    def is_production(self) -> bool:
        return self.env == Environment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.env == Environment.DEVELOPMENT
