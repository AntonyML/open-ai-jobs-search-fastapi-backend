"""LLM Orchestrator — resilient, multi-provider execution engine.

All LLM calls in the application MUST route through this orchestrator.
No service should call LiteLLM or llm_completion_structured directly.

Architecture:
    LLMOrchestrator (public API)
        ├── ProviderManager (provider registry, health, priorities)
        ├── ModelManager (model registry, states, selection)
        ├── ExecutionQueue (concurrency, checkpointing, persistence)
        └── LLMResponseSanitizer (repair before Pydantic validation)
"""

from app.services.orchestrator.llm_orchestrator import LLMOrchestrator

__all__ = ["LLMOrchestrator"]
