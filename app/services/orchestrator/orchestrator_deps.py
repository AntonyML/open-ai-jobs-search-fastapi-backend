"""FastAPI dependency for the LLMOrchestrator singleton.

This module provides a singleton orchestrator instance that is shared
across all requests. The orchestrator is created once at application
startup and reused for the lifetime of the process.

Usage in routers:
    orchestrator = Depends(get_orchestrator)
"""

from __future__ import annotations

from functools import lru_cache

from app.services.orchestrator import LLMOrchestrator


@lru_cache
def get_orchestrator() -> LLMOrchestrator:
    """Return the singleton LLMOrchestrator instance.

    The LRU cache ensures only one instance is created per process.
    Configure max_concurrency via settings or environment variable.
    """
    from app.core.settings import get_settings

    settings = get_settings()
    # Default to 4 workers; can be configured via ORCHESTRATOR_MAX_CONCURRENCY env var
    max_concurrency = getattr(settings, "orchestrator_max_concurrency", 4)
    return LLMOrchestrator(max_concurrency=max_concurrency)
