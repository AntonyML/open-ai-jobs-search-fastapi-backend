"""FastAPI application factory.

Usage:
    from app.main import create_app
    app = create_app()

    # With custom settings (useful for tests):
    app = create_app(settings=test_settings)
"""

import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.core.logging import get_logger, setup_logging
from app.core.logging.middleware import RequestLoggingMiddleware

logger = get_logger("app")
from app.core.settings import Settings, get_settings
from app.exceptions import AppError, app_error_handler, validation_error_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown logic for the app."""
    yield
    # Shutdown: wait for background tasks, then dispose engine
    from app.core.task_manager import background_tasks

    await background_tasks.shutdown()

    from app.db.session import engine

    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured FastAPI instance.

    Args:
        settings: Optional Settings override (defaults to get_settings()).

    Returns:
        A FastAPI app ready to be served by uvicorn.
    """
    if settings is None:
        settings = get_settings()

    # Logging BEFORE everything else
    setup_logging()

    # SQLAlchemy query logging (goes to logs/sql.log, not terminal)
    from app.core.logging.interceptors import setup_sqlalchemy_logging
    from app.db.session import engine
    setup_sqlalchemy_logging(engine)

    # ── Create FastAPI app ─────────────────────────────────────
    app = FastAPI(
        title="Open Ai Jobs Search API",
        version="0.1.0",
        description="Backend multi-proveedor para búsqueda de empleos con IA",
        lifespan=lifespan,
    )

    # ── CORS ───────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request logging middleware (request_id, latency, access.log) ─
    app.add_middleware(RequestLoggingMiddleware)

    # ── Exception handlers ─────────────────────────────────────
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled exceptions."""
        logger.error(
            "Unhandled exception at %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        if settings.sentry_dsn:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)

        origin = request.headers.get("origin", "")
        allow_origin = origin if origin in settings.cors_origins else (settings.cors_origins[0] if settings.cors_origins else "")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
            headers={
                "Access-Control-Allow-Origin": allow_origin,
                "Access-Control-Allow-Credentials": "true",
            },
        )

    # ── Health check ───────────────────────────────────────────
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # ── Routers ────────────────────────────────────────────────
    app.include_router(v1_router, prefix="/api/v1")

    return app


app = create_app()
