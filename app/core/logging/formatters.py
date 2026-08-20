"""Log formatters: Console (human-friendly), File (detailed), JSON (structured)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.logging.context import get_context

_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_RESET = "\033[0m"
_DIM = "\033[2m"


class ConsoleFormatter(logging.Formatter):
    """Clean terminal output.

    Dev:  14:32:07 │ INFO  │ orchestrator │ Provider anthropic healthy [req=a1b stage=rank]
    """

    def __init__(self, colorize: bool = True):
        super().__init__()
        self.colorize = colorize and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        ctx = get_context()
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = record.levelname.ljust(7)
        name = record.name.replace("app.", "").split(".")[0] if record.name else "app"

        ctx_parts = []
        if ctx.request_id:
            ctx_parts.append(f"req={ctx.request_id}")
        if ctx.stage:
            ctx_parts.append(f"stage={ctx.stage}")
        if ctx.job_id:
            ctx_parts.append(f"job={ctx.job_id[:8]}")
        ctx_str = f" [{', '.join(ctx_parts)}]" if ctx_parts else ""

        msg = record.getMessage()

        if self.colorize:
            color = _COLORS.get(record.levelname, "")
            line = f"{_DIM}{ts}{_RESET} │ {color}{level}{_RESET} │ {_DIM}{name}{_RESET} │ {msg}{ctx_str}"
        else:
            line = f"{ts} │ {level} │ {name} │ {msg}{ctx_str}"

        if record.exc_info and record.levelno >= logging.ERROR:
            line += "\n" + self.formatException(record.exc_info)

        return line


class FileFormatter(logging.Formatter):
    """Detailed format for .log files with full context.

    2026-07-24 14:32:07.123 │ INFO │ app.services.orchestrator │ [req=a1b2c3 stage=rank] │ msg
    """

    def format(self, record: logging.LogRecord) -> str:
        ctx = get_context()
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        level = record.levelname.ljust(7)
        ctx_dict = ctx.as_dict()
        ctx_str = f"[{' '.join(f'{k}={v}' for k, v in ctx_dict.items())}]" if ctx_dict else "[]"

        msg = record.getMessage()
        line = f"{ts} │ {level} │ {record.name} │ {ctx_str} │ {msg}"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


class JsonFormatter(logging.Formatter):
    """JSON structured for production (stdout → Fly logs / Datadog).

    {"ts":"...","level":"INFO","logger":"app.services.rank","msg":"...","request_id":"a1b2c3",...}
    """

    def format(self, record: logging.LogRecord) -> str:
        ctx = get_context()
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **ctx.as_dict(),
        }
        if hasattr(record, "extra_data"):
            entry.update(record.extra_data)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)
