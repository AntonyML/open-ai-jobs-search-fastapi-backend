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

import re
from typing import Any
from app.core.logging import get_logger

logger = get_logger(__name__)


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


# Fields that MUST be strings in the final schema.
# The LLM sometimes returns a single-element array (e.g. ["2024"]) where
# a plain string is expected.  We detect and unwrap them here.
_STRING_FIELDS: set[str] = {
    "period", "year", "url", "degree", "institution", "company",
    "title", "name", "location", "salary", "description",
    "start", "end", "label", "proficiency",
    "issuer", "journal", "doi", "authors", "degree",
}


def _sanitize_values(
    data: dict[str, Any],
    field_constraints: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Normalize all values in the parsed dict.

    - Truncate arrays to max_length
    - Trim strings
    - Convert numbers to int where appropriate
    - Fill in defaults for None values where possible
    - **Deep recursion**: handles nested dicts and lists of dicts
    - **Proficiency mapping**: maps LLM-common values (``'native'``, ``'fluent'``)
      to valid ``ProficiencyLevel`` literals
    - **Null date_range**: replaces explicit ``None`` with
      ``{"start": None, "end": None}`` so Pydantic's ``default_factory`` works
    - **Array-to-scalar**: single-element arrays in ``_STRING_FIELDS`` are
      unwrapped to their scalar value (``["2024"]`` → ``"2024"``)
    """
    sanitized: dict[str, Any] = {}
    constraints = field_constraints or {}

    for key, value in data.items():
        if value is None:
            # ── Special handling for fields that can be null ──────
            if key == "date_range":
                # Pydantic DateRange has default_factory but rejects
                # explicit None.  Replace with empty DateRange.
                sanitized[key] = {"start": None, "end": None}
            else:
                sanitized[key] = None
            continue

        # ── Array-to-scalar: LLM sometimes returns ["2024"] for a string field ─
        if isinstance(value, list) and key in _STRING_FIELDS:
            # Join all non-None items into a comma-separated string
            sanitized[key] = ", ".join(str(v) for v in value if v is not None)
            continue

        # String values
        if isinstance(value, str):
            sanitized[key] = _sanitize_string_value(key, value)

        # Integer values
        elif isinstance(value, (int, float)):
            sanitized[key] = value

        # List values (truncate to max_length + recurse into dict items)
        elif isinstance(value, list):
            max_len = constraints.get(key, {}).get("max_length")
            if max_len and len(value) > max_len:
                logger.warning(
                    "Truncating field '%s' from %d items to %d",
                    key, len(value), max_len,
                )
                value = value[:max_len]
            sanitized[key] = [
                _sanitize_values(item, constraints)
                if isinstance(item, dict)
                else str(item).strip() if isinstance(item, str)
                else item
                for item in value
            ]

        # Dict values (recurse)
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_values(value, constraints)

        # Boolean values
        elif isinstance(value, bool):
            sanitized[key] = value

        # Fallback: convert to string
        else:
            sanitized[key] = str(value)

    return sanitized


def _sanitize_string_value(key: str, value: str) -> str:
    """Sanitize a single string value, with special handling per field key."""
    stripped = value.strip()

    # ── Proficiency mapping ───────────────────────────────────────
    # The LLM often returns 'native', 'fluent', 'master' for CV skills,
    # but ProficiencyLevel only accepts: beginner/intermediate/advanced/expert.
    if key == "proficiency":
        return _map_proficiency(stripped)

    return stripped


def _map_proficiency(value: str) -> str:
    """Map an LLM proficiency string to a valid ProficiencyLevel literal.

    ``ProficiencyLevel = Literal["beginner", "intermediate", "advanced", "expert"]``
    """
    mapping = {
        "native": "expert",
        "fluent": "advanced",
        "master": "expert",
        "proficient": "advanced",
        "expert": "expert",
        "advanced": "advanced",
        "intermediate": "intermediate",
        "beginner": "beginner",
    }
    key = value.lower().strip()
    if key in mapping:
        return mapping[key]
    # Unknown proficiency value — default to "intermediate"
    logger.warning("Unknown proficiency value '%s', defaulting to 'intermediate'", value)
    return "intermediate"


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
