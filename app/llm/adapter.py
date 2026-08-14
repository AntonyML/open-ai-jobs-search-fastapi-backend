"""LiteLLM adapter — multi-provider LLM interface.

All LLM calls go through this module.  The active provider is read from
the user's configuration (stored in Supabase), not hardcoded.
"""

from __future__ import annotations

import json
from typing import Any

import litellm
from litellm import responses, supports_web_search

from app.core.settings import get_settings
from app.exceptions import LLMError, ProviderAuthError

settings = get_settings()

# Tell LiteLLM to not crash on missing keys at import time — we validate
# at call time instead.
litellm.suppress_debug_info = True
litellm.num_retries = 0
litellm.request_timeout = settings.llm_timeout


def _build_kwargs(
    provider: str,
    model: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> dict[str, Any]:
    """Build the kwargs dict for a LiteLLM completion call."""
    kwargs: dict[str, Any] = {"model": f"{provider}/{model}"}

    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base

    return kwargs


def get_provider_kwargs(provider_config: dict | None) -> dict[str, Any]:
    """Extract provider kwargs from a provider config dict.

    Args:
        provider_config: Dict with keys "provider", "model", "api_key", "api_base"
            or None to use defaults from settings.

    Returns:
        Dict with provider, model, api_key, api_base for LLM calls.
    """
    if provider_config is None:
        return {
            "provider": settings.llm_default_provider,
            "model": "claude-sonnet-4-20250514",
            "api_key": None,
            "api_base": None,
        }

    return {
        "provider": provider_config.get("provider", settings.llm_default_provider),
        "model": provider_config.get("model", "claude-sonnet-4-20250514"),
        "api_key": provider_config.get("api_key"),
        "api_base": provider_config.get("api_base"),
    }


async def llm_completion(
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    api_key: str | None = None,
    api_base: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: dict[str, Any] | None = None,
) -> str:
    """Send a chat completion request through LiteLLM.

    Args:
        messages: List of {"role": "...", "content": "..."} dicts.
        provider: e.g. "anthropic", "openai", "nvidia_nim", "lm_studio".
            Falls back to settings.llm_default_provider.
        model: Model name (without provider prefix).
        api_key: Provider API key. Falls back to settings env vars.
        api_base: Base URL for self-hosted / local providers.
        temperature: Sampling temperature.
        max_tokens: Max tokens in the response.
        response_format: Optional JSON schema for structured output.

    Returns:
        The text content of the first choice.

    Raises:
        ProviderAuthError: No API key configured for the provider.
        LLMError: The LLM call failed.
    """
    provider = provider or settings.llm_default_provider

    # Resolve API key
    if api_key is None:
        key_map = {
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
            "nvidia_nim": settings.nvidia_nim_api_key,
        }
        api_key = key_map.get(provider)

    if api_key is None and provider not in ("lm_studio",):
        raise ProviderAuthError(
            f"No API key configured for provider '{provider}'. "
            f"Set it in .env or store it encrypted in your user profile."
        )

    if api_base is None and provider == "lm_studio":
        api_base = settings.lm_studio_api_base

    kwargs = _build_kwargs(provider, model, api_key, api_base)
    kwargs["temperature"] = temperature
    kwargs["max_tokens"] = max_tokens
    kwargs["timeout"] = settings.llm_timeout
    kwargs["num_retries"] = 0

    if response_format:
        kwargs["response_format"] = response_format

    try:
        response = await litellm.acompletion(messages=messages, **kwargs)
        message = response.choices[0].message
        content = message.content or getattr(message, "reasoning_content", None)
        if content is None:
            raise LLMError("LLM returned empty response")
        return content
    except litellm.exceptions.AuthenticationError as exc:
        raise ProviderAuthError(str(exc)) from exc
    except litellm.exceptions.BadRequestError as exc:
        raise LLMError(f"LLM request error: {exc}") from exc
    except litellm.exceptions.RateLimitError as exc:
        raise LLMError(f"LLM rate-limited: {exc}") from exc
    except litellm.exceptions.Timeout as exc:
        raise LLMError(f"LLM timed out: {exc}") from exc
    except Exception as exc:
        raise LLMError(f"LLM call failed: {exc}") from exc


def has_web_search_support(model: str) -> bool:
    """Whether the given provider/model supports the OpenAI ``web_search`` tool.

    Only models with native web access (e.g. ``gpt-5``, ``gpt-4o-search-preview``)
    can open a URL at inference time. The provider's own infrastructure fetches
    the page — our backend never scrapes.
    """
    try:
        return bool(supports_web_search(model))
    except Exception:
        return False


async def llm_completion_with_web_search(
    messages: list[dict[str, str]],
    *,
    provider: str,
    model: str,
    api_key: str | None = None,
    api_base: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Chat completion with the OpenAI ``web_search`` tool (Responses API).

    Use when the prompt references a public URL (e.g. "adapt my CV to the job
    at <link>"): the provider fetches and reads the page under its own
    agreements, so we never scrape from our servers. Only works for models
    with web access (see ``has_web_search_support``).

    Raises ``ProviderAuthError`` / ``LLMError`` like ``llm_completion``.
    """
    model_ref = f"{provider}/{model}"

    try:
        response = await responses(
            input=[
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages
            ],
            model=model_ref,
            api_key=api_key,
            api_base=api_base,
            tools=[{"type": "web_search"}],
            max_output_tokens=max_tokens,
            timeout=settings.llm_timeout,
        )
    except litellm.exceptions.AuthenticationError as exc:
        raise ProviderAuthError(str(exc)) from exc
    except litellm.exceptions.BadRequestError as exc:
        raise LLMError(f"LLM request error: {exc}") from exc
    except litellm.exceptions.RateLimitError as exc:
        raise LLMError(f"LLM rate-limited: {exc}") from exc
    except litellm.exceptions.Timeout as exc:
        raise LLMError(f"LLM timed out: {exc}") from exc
    except Exception as exc:
        raise LLMError(f"LLM call failed: {exc}") from exc

    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        content = getattr(item, "content", None)
        if not content:
            continue
        for part in content:
            text = getattr(part, "text", None)
            if text:
                parts.append(text)
    text = "\n".join(parts).strip()
    if not text:
        raise LLMError("LLM returned empty response")
    return text


async def llm_completion_structured(
    messages: list[dict[str, str]],
    output_schema: type,
    *,
    provider: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    api_key: str | None = None,
    api_base: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> Any:
    """Like llm_completion, but parses the response into a Pydantic model.

    Uses LiteLLM's response_format / tool-calling under the hood and
    validates the result with the provided Pydantic schema.
    """
    import pydantic

    schema_name = output_schema.__name__
    schema_json = output_schema.model_json_schema()

    raw = await llm_completion(
        messages=messages,
        provider=provider,
        model=model,
        api_key=api_key,
        api_base=api_base,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={
            "type": "json_object",
            "response_schema": {
                "name": schema_name,
                "schema": schema_json,
                "strict": True,
            },
        },
    )

    try:
        data = json.loads(raw)
        return output_schema.model_validate(data)
    except (json.JSONDecodeError, pydantic.ValidationError) as exc:
        raise LLMError(f"LLM response failed schema validation for {schema_name}: {exc}") from exc
