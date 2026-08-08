"""Application settings loaded from environment variables.

Uses pydantic-settings for validation and .env file support.
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised configuration for the FastAPI backend.

    All secrets come from environment variables — never hardcoded.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Supabase / PostgreSQL ----------------------------------
    database_url: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/postgres"
    )

    # -- LiteLLM / LLM Providers -------------------------------
    llm_default_provider: str = "anthropic"
    llm_timeout: int = 180
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    nvidia_nim_api_key: str | None = None
    lm_studio_api_base: str = "http://localhost:1234/v1"

    # -- Auth / JWT --------------------------------------------
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # -- Resend (Email) ----------------------------------------
    resend_api_key: str | None = None
    resend_from_email: str = "onboarding@resend.dev"
    admin_email: str = "admin@openajobs.com"

    # -- Rate Limiting -----------------------------------------
    rate_limit_attempts: int = 5
    rate_limit_window_seconds: int = 900  # 15 minutes

    # -- i18n --------------------------------------------------
    default_language: str = "en"

    # -- Microservice Ingesta ----------------------------------
    ingest_service_url: str = "http://localhost:8001"

    # -- Sentry ------------------------------------------------
    sentry_dsn: str | None = None

    # -- App ---------------------------------------------------
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]

    # -- Paths -------------------------------------------------
    documents_dir: str = "documents"
    tracker_path: str = "documents/tracker.json"

    # -- Orchestrator -------------------------------------------
    # Max concurrent LLM workers in the execution queue
    orchestrator_max_concurrency: int = 4



@lru_cache
def get_settings() -> Settings:
    """Return cached Settings singleton."""
    return Settings()