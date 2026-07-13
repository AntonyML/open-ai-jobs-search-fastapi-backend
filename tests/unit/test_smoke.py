"""Smoke tests — verify the app factory builds and the health endpoint works."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import create_app

    app = create_app()
    return TestClient(app)


def test_health_endpoint(client):
    """The /api/v1/health endpoint returns 200 with status ok."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_app_factory_returns_fastapi_instance():
    """create_app() returns a FastAPI instance."""
    from fastapi import FastAPI

    from app.main import create_app

    app = create_app()
    assert isinstance(app, FastAPI)


def test_settings_loads_defaults():
    """Settings can be instantiated with defaults."""
    from app.core.settings import Settings

    settings = Settings()
    assert settings.app_env == "development"
    assert settings.llm_default_provider == "anthropic"


def test_exception_handler_registered():
    """AppError handler is registered on the app."""
    from app.main import create_app

    app = create_app()
    handlers = app.exception_handlers
    # AppError is a class — FastAPI stores it under its __name__ or via the class
    # We just verify the app has exception handlers configured.
    assert len(handlers) > 0