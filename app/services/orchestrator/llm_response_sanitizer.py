"""LLM response sanitizer — repairs and normalises raw LLM output before Pydantic.

Never fail because the model returned 8 keywords instead of 5.
Strategy:
1. Attempt direct JSON parse
2. If that fails, try to extract JSON from markdown code blocks
3. Sanitize the parsed data: truncate arrays, trim strings, fill defaults
4. Only reject responses that are truly unrecoverable

This is the LAST line of defense — prompts should already constrain output.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def sanitize_llm_response(
    raw_text: str,
    schema_name: str,
    field_constraints: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Parse and sanitize a raw LLM response string into a safe dict.

    Args:
        raw_text: The raw string returned by the LLM.
        schema_name: Name of the target Pydantic schema (for logging).
        field_constraints: Optional dict mapping field names to length limits
            (e.g. {'strengths': {'max_length': 3}}).

    Returns:
        A sanitized dict ready for Pydantic model_validate().

    Raises:
        ValueError: If the response cannot be parsed at all.
    """
    # Step 1: Extract JSON from the raw text
    parsed = _extract_json(raw_text, schema_name)

    # Step 2: Sanitize the parsed data
    sanitized = _sanitize_values(parsed, field_constraints)

    return sanitized


def _extract_json(raw_text: str, schema_name: str) -> dict[str, Any]:
    """Extract a JSON object from the raw LLM response.

    Tries:
    1. Direct json.loads
    2. Extract from ```json ... ``` markdown block
    3. Extract from ``` ... ``` block (any language)
    4. Find first { ... } with balanced braces
    """
    text = raw_text.strip()

    # Try 1: Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try 2: Markdown json code block
    json_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(json_pattern, text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Try 3: Find JSON object with balanced braces
    brace_depth = 0
    start_idx = -1
    for i, char in enumerate(text):
        if char == "{":
            if start_idx == -1:
                start_idx = i
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
            if brace_depth == 0 and start_idx != -1:
                candidate = text[start_idx : i + 1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    pass
                start_idx = -1

    # Try 4: Single-quote JSON (common LLM mistake)
    single_quote_pattern = r"\{[^}]+\}"
    match = re.search(single_quote_pattern, text, re.DOTALL)
    if match:
        candidate = match.group(0)
        # Replace single quotes with double quotes (carefully)
        candidate = _fix_single_quotes(candidate)
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not extract valid JSON from LLM response for {schema_name}. "
        f"Response preview: {text[:200]}"
    )


def _fix_single_quotes(text: str) -> str:
    """Replace single-quoted strings with double-quoted strings.

    Handles: { 'key': 'value' } -> { "key": "value" }
    """
    # Replace keys (single-quoted before colon)
    text = re.sub(r"'([^']+)'(?=\s*:)", r'"\1"', text)
    # Replace string values (single-quoted after colon/bracket/comma)
    text = re.sub(r"(?<=[:,\[ ])'([^']*)'(?=\s*[,:\}\]])", r'"\1"', text)
    return text


def _sanitize_values(
    data: dict[str, Any],
    field_constraints: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Normalize all values in the parsed dict.

    - Truncate arrays to max_length
    - Trim strings
    - Convert numbers to int where appropriate
    - Fill in defaults for None values where possible
    """
    sanitized: dict[str, Any] = {}
    constraints = field_constraints or {}

    for key, value in data.items():
        if value is None:
            sanitized[key] = None
            continue

        # String values
        if isinstance(value, str):
            sanitized[key] = value.strip()

        # Integer values
        elif isinstance(value, (int, float)):
            sanitized[key] = value

        # List values (truncate to max_length if specified)
        elif isinstance(value, list):
            max_len = constraints.get(key, {}).get("max_length")
            if max_len and len(value) > max_len:
                logger.warning(
                    "Truncating field '%s' from %d items to %d",
                    key, len(value), max_len,
                )
                sanitized[key] = [str(v).strip() for v in value[:max_len]]
            else:
                sanitized[key] = [str(v).strip() if not isinstance(v, (int, float)) else v for v in value]

        # Dict values (pass through)
        elif isinstance(value, dict):
            sanitized[key] = value

        # Boolean values
        elif isinstance(value, bool):
            sanitized[key] = value

        # Fallback: convert to string
        else:
            sanitized[key] = str(value)

    return sanitized


def default_field_constraints() -> dict[str, dict[str, int]]:
    """Return the default field constraints for common LLM output schemas.

    These prevent array-length validation failures from crashing the pipeline.
    """
    return {
        "strengths": {"max_length": 3},
        "gaps": {"max_length": 3},
        "missing_keywords": {"max_length": 5},
        "red_flags": {"max_length": 3},
        "questions": {"max_length": 15},
        "mappings": {"max_length": 15},
        "drafts": {"max_length": 10},
        "claims": {"max_length": 10},
        "additions": {"max_length": 20},
        "enrichments": {"max_length": 50},
        "items": {"max_length": 50},
        "gaps_list": {"max_length": 20},
        "heatmap": {"max_length": 30},
        "plan": {"max_length": 20},
        "resources": {"max_length": 5},
        "experience": {"max_length": 10},
        "body_paragraphs": {"max_length": 4},
        "hooks": {"max_length": 5},
        "questions_to_ask": {"max_length": 8},
        "tough_questions": {"max_length": 8},
        "incorporated_keywords": {"max_length": 10},
        "addressed_red_flags": {"max_length": 10},
        "source_jobs": {"max_length": 20},
        "source_urls": {"max_length": 5},
    }
