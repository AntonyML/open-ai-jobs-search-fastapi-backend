"""Tests for the adapt-by-URL flow (the model reads the link, we never scrape)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import LLMError, ProviderAuthError, WebSearchUnavailableError
from app.llm.adapter import has_web_search_support, llm_completion_with_web_search
from app.services import apply_json
from app.services.apply_json import adapt_cv_llm_with_url

SAMPLE_ANALYSIS = {
    "match_score": 78,
    "missing_keywords": ["Kubernetes"],
    "red_flags": ["Few senior years"],
    "adapted_experience": ["Lead the X bullet with scale"],
}

SAMPLE_OUTPUT = {
    "cv": {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "experience": [],
        "profile_statement": "Results-driven engineer.",
    },
    "metadata": {"language": "en"},
}

CANDIDATE = MagicMock()
BASE_CV_JSON = {"cv": {"first_name": "Jane"}}
URL = "https://www.linkedin.com/jobs/view/4415693439"


# ── has_web_search_support ─────────────────────────────────────────


def test_has_web_search_support_true_for_search_models():
    assert has_web_search_support("openai/gpt-5") is True
    assert has_web_search_support("openai/gpt-4o-search-preview") is True


def test_has_web_search_support_false_for_plain_models():
    assert has_web_search_support("openai/gpt-4o") is False
    assert has_web_search_support("anthropic/claude-sonnet-4-20250514") is False


# ── llm_completion_with_web_search (adapter) ───────────────────────


class FakeTextPart:
    def __init__(self, text):
        self.text = text


class FakeOutputItem:
    def __init__(self, texts):
        self.content = [FakeTextPart(t) for t in texts]


class FakeResponse:
    def __init__(self, items):
        self.output = items


async def test_llm_completion_with_web_search_extracts_text():
    resp = FakeResponse([FakeOutputItem(['{"match_score": 80}']), FakeOutputItem(["", "done"])])
    with patch("app.llm.adapter.responses", new=AsyncMock(return_value=resp)) as mock_responses:
        text = await llm_completion_with_web_search(
            messages=[{"role": "user", "content": f"Read {URL} and adapt"}],
            provider="openai",
            model="gpt-5",
        )

    assert text == '{"match_score": 80}\ndone'
    kwargs = mock_responses.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-5"
    assert kwargs["tools"] == [{"type": "web_search"}]


async def test_llm_completion_with_web_search_raises_on_empty():
    with patch("app.llm.adapter.responses", new=AsyncMock(return_value=FakeResponse([]))):
        with pytest.raises(LLMError):
            await llm_completion_with_web_search(
                messages=[{"role": "user", "content": "hi"}],
                provider="openai",
                model="gpt-5",
            )


async def test_llm_completion_with_web_search_auth_error():
    from litellm.exceptions import AuthenticationError

    with patch(
        "app.llm.adapter.responses",
        new=AsyncMock(
            side_effect=AuthenticationError("bad key", llm_provider="openai", model="gpt-5")
        ),
    ):
        with pytest.raises(ProviderAuthError):
            await llm_completion_with_web_search(
                messages=[{"role": "user", "content": "hi"}],
                provider="openai",
                model="gpt-5",
            )


# ── adapt_cv_llm_with_url (service) ────────────────────────────────


async def test_adapt_cv_llm_with_url_rejects_model_without_web_search():
    """A model without web access fails fast — before any LLM call."""
    with (
        patch("app.services.apply_json.has_web_search_support", return_value=False),
        patch("app.services.apply_json._llm_json", new=AsyncMock()) as mock_llm,
        pytest.raises(WebSearchUnavailableError),
    ):
        await adapt_cv_llm_with_url(
            CANDIDATE, BASE_CV_JSON, URL, {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}
        )

    mock_llm.assert_not_awaited()


async def test_adapt_cv_llm_with_url_passes_url_and_web_search():
    """With web access, the URL goes into the prompt and _llm_json uses web_search."""
    with (
        patch("app.services.apply_json.has_web_search_support", return_value=True),
        patch(
            "app.services.apply_json._llm_json",
            new=AsyncMock(side_effect=[SAMPLE_ANALYSIS, SAMPLE_OUTPUT]),
        ) as mock_llm,
    ):
        analysis, output = await adapt_cv_llm_with_url(
            CANDIDATE, BASE_CV_JSON, URL, {"provider": "openai", "model": "gpt-5"}
        )

    assert analysis == SAMPLE_ANALYSIS
    assert output == SAMPLE_OUTPUT

    calls = mock_llm.await_args_list
    assert len(calls) == 2
    # Both calls route through the web-search adapter with the URL in the prompt.
    for call in calls:
        assert call.kwargs.get("web_search") is True
        prompt = call.args[0][-1]["content"]
        assert URL in prompt
