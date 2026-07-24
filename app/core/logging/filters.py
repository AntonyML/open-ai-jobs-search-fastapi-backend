"""Log filters: domain routing, healthcheck suppression, PII redaction."""

from __future__ import annotations

import logging
import re


class DomainFilter(logging.Filter):
    """Only pass records from a specific domain logger.

    Usage: handler for orchestrator.log gets DomainFilter("orchestrator")
    """

    def __init__(self, domain: str):
        super().__init__()
        self._check = f"app.{domain}"

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self._check)


class HealthCheckFilter(logging.Filter):
    """Suppress healthcheck requests from access.log."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/health" not in msg


class PIIFilter(logging.Filter):
    """Redact emails and API keys/tokens from log messages."""

    _EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    _TOKEN_RE = re.compile(r"(sk-|key-|Bearer |nvapi-)[a-zA-Z0-9\-_]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._EMAIL_RE.sub("[EMAIL]", record.msg)
            record.msg = self._TOKEN_RE.sub("[TOKEN]", record.msg)
        if record.args:
            cleaned = []
            for arg in record.args:
                if isinstance(arg, str):
                    arg = self._EMAIL_RE.sub("[EMAIL]", arg)
                    arg = self._TOKEN_RE.sub("[TOKEN]", arg)
                cleaned.append(arg)
            record.args = tuple(cleaned)
        return True
