"""Application settings loaded from environment variables.

Uses pydantic-settings for validation and .env file support.
"""

from functools import lru_cache
from pathlib import Path

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

    # ── Supabase / PostgreSQL ──────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/postgres"
    )

    # ── LiteLLM / LLM Providers ───────────────────────────────
    llm_default_provider: str = "anthropic"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    nvidia_nim_api_key: str | None = None
    lm_studio_api_base: str = "http://localhost:1234/v1"

    # ── Auth / JWT ────────────────────────────────────────────
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # ── APScheduler ───────────────────────────────────────────
    scrape_interval_hours: int = 6

    # ── App ───────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    # ── Paths ─────────────────────────────────────────────────
    @property
    def latex_cv_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "external" / "latex" / "cv"

    @property
    def latex_cover_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "external" / "latex" / "cover_letters"

    @property
    def scrapers_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "external" / "scrapers"


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings singleton."""
    return Settings()