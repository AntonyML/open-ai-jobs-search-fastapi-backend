"""FastAPI middleware — request_id, latency, access log, context binding."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging.context import (
    LogContext,
    new_request_id,
    set_context,
    reset_context,
)

access_logger = logging.getLogger("app.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Assign request_id, measure latency, log one access line per request."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = new_request_id()
        user_id = getattr(request.state, "user_id", "")
        if not user_id:
            user = getattr(request.state, "user", None)
            if isinstance(user, dict):
                user_id = user.get("sub", "")

        ctx = LogContext(
            request_id=request_id,
            user_id=user_id,
            method=request.method,
            path=request.url.path,
        )
        token = set_context(ctx)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            reset_context(token)

        elapsed_ms = (time.perf_counter() - start) * 1000

        access_logger.info(
            "%s %s → %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            extra={"extra_data": {
                "request_id": request_id,
                "user_id": user_id,
                "status": response.status_code,
                "latency_ms": round(elapsed_ms, 1),
            }},
        )

        response.headers["X-Request-ID"] = request_id
        return response
