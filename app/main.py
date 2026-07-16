"""FastAPI application factory.

Usage:
    from app.main import create_app
    app = create_app()

    # With custom settings (useful for tests):
    app = create_app(settings=test_settings)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.core.logging import configure_logging
from app.core.settings import Settings, get_settings
from app.exceptions import AppError, app_error_handler, validation_error_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown logic for the app."""
    # Configure structured logging
    configure_logging()

    # Startup: start APScheduler for periodic scraping
    from app.core.scheduler import scheduler_lifespan

    async with scheduler_lifespan():
        yield
    # Shutdown: dispose the SQLAlchemy engine
    from app.core.scheduler import shutdown_scheduler

    shutdown_scheduler()
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

    # ── Exception handlers ─────────────────────────────────────
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
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

    # ── Routers ────────────────────────────────────────────────
    app.include_router(v1_router, prefix="/api/v1")

    return app
