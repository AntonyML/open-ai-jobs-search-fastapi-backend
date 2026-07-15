"""Content Guard — middleware that verifies generated outputs don't leak personal data.

Adapted from MadsLorentzen/ai-job-search tools/security_guards.py.

Checks generated content (CV text, cover letter text, LaTeX) for:
1. Sensitive personal information that shouldn't appear in outputs (SSN, bank details)
2. Personal data appearing in places where it shouldn't (e.g., phone in cover letter when not requested)
3. Placeholder tokens that weren't replaced ([YOUR_NAME], [COMPANY], etc.)

This is NOT a replacement for proper data sanitization — it's a defense-in-depth
check that runs AFTER content generation to catch leaks before they're saved or sent.

The guard can be used as:
- A middleware function that wraps generated content
- A standalone utility for manual checking
- An integration into the /apply pipeline after LLM generation

100% DETERMINISTIC — no LLM calls. Uses regex patterns.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Patterns ─────────────────────────────────────────────────────────

# Sensitive personal data patterns (SHOULD NEVER appear in generated content)
_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),  # US SSN
    ("bank_account", re.compile(r"\b\d{8,17}\b(?:\s*(?:bank|account|acct))", re.IGNORECASE)),  # Bank account
    ("credit_card", re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")),  # Credit card number
    # Passport number heuristic: 1-2 uppercase letters + 6-9 digits
    # NOTE: This pattern will produce false positives for generic alphanumeric
    # codes (e.g., "AB1234567"). It's a heuristic-only check — not a reliable
    # passport detector. Consider tightening with context in production.
    ("passport", re.compile(r"(?:passport|pp(?:\s|\.)?\s*)?\b[A-Z]{1,2}\d{6,9}\b", re.IGNORECASE)),
    ("driver_license", re.compile(r"\b[A-Z]{1,3}\d{4,8}\b(?:driver|dl)", re.IGNORECASE)),  # Driver license
]

# Placeholder patterns (SHOULD BE REPLACED before final output)
_PLACEHOLDER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("name_placeholder", re.compile(r"\[YOUR[_ ]?NAME\]", re.IGNORECASE)),
    ("company_placeholder", re.compile(r"\[COMPANY[_ ]?NAME\]", re.IGNORECASE)),
    ("role_placeholder", re.compile(r"\[JOB[_ ]?TITLE\]|\[ROLE\]|\[POSITION\]", re.IGNORECASE)),
    ("date_placeholder", re.compile(r"\[DATE\]|\[CURRENT_DATE\]", re.IGNORECASE)),
    ("generic_placeholder", re.compile(r"\[([A-Z_]+)\]")),  # Any remaining [PLACEHOLDER]
]

# Contact info patterns (for checking where contact info appears)
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_PATTERN = re.compile(r"\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")


# ── Result ───────────────────────────────────────────────────────────


class ContentGuardResult:
    """Result of a content guard check."""

    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self.placeholders_found: list[str] = []
        self.sensitive_data_found: list[str] = []
        self.contact_info_found: bool = False

    @property
    def passed(self) -> bool:
        """Check if all guard conditions pass."""
        return (
            len(self.sensitive_data_found) == 0
            and len(self.placeholders_found) == 0
        )

    @property
    def summary(self) -> str:
        parts = []
        if self.placeholders_found:
            parts.append(f"{len(self.placeholders_found)} placeholder(s)")
        if self.sensitive_data_found:
            parts.append(f"{len(self.sensitive_data_found)} sensitive data leak(s)")
        if not parts:
            return "Content guard passed"
        return ", ".join(parts)


# ── Check functions ─────────────────────────────────────────────────


def check_sensitive_data(content: str) -> list[str]:
    """Check for sensitive personal data in content.

    Args:
        content: The generated text content to check.

    Returns:
        List of sensitive data types found (e.g., ["ssn", "credit_card"]).
    """
    found: list[str] = []
    for name, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(content):
            found.append(name)
            logger.warning(f"Content guard: sensitive data pattern detected: {name}")
    return found


def check_placeholders(content: str) -> list[str]:
    """Check for unreplaced placeholder tokens.

    Args:
        content: The generated text content to check.

    Returns:
        List of placeholder patterns found.
    """
    found: list[str] = []
    for name, pattern in _PLACEHOLDER_PATTERNS:
        if pattern.search(content):
            found.append(name)
            logger.warning(f"Content guard: placeholder token detected: {name}")
    return found


def check_contact_info_location(
    content: str,
    location: str,
    allow_contact: bool = False,
) -> list[str]:
    """Check if contact info appears in unexpected locations.

    Args:
        content: The generated text content.
        location: Where this content appears ("cv", "cover_letter", "email_body").
        allow_contact: If True, contact info is expected (e.g., in a CV header).

    Returns:
        List of issues found.
    """
    issues: list[str] = []

    if allow_contact:
        return issues  # Contact info is expected here

    emails = _EMAIL_PATTERN.findall(content)
    phones = _PHONE_PATTERN.findall(content)

    if emails and location == "cover_letter":
        issues.append(f"Email(s) found in cover letter: {', '.join(emails[:2])}")

    if phones and location == "cover_letter":
        issues.append(f"Phone number(s) found in cover letter")

    return issues


# ── Main entry point ────────────────────────────────────────────────


def guard_content(
    content: str,
    content_type: str = "generic",
    allow_contact: bool = False,
) -> ContentGuardResult:
    """Run all content guard checks on generated content.

    Args:
        content: The generated text content to check.
        content_type: Type of content ("cv", "cover_letter", "latex", "generic").
        allow_contact: If True, contact info is expected (e.g., CV header).

    Returns:
        ContentGuardResult with all issues found.

    Example:
        ```python
        result = guard_content(
            content=cv_latex,
            content_type="latex",
            allow_contact=True,  # CV header has contact info
        )
        if not result.passed:
            print(f"Issues found: {result.summary}")
            for issue in result.sensitive_data_found:
                print(f"  Sensitive data: {issue}")
        ```
    """
    result = ContentGuardResult()

    # 1. Check sensitive data
    result.sensitive_data_found = check_sensitive_data(content)
    for data_type in result.sensitive_data_found:
        result.issues.append({
            "type": "sensitive_data",
            "detail": data_type,
            "severity": "critical",
        })

    # 2. Check placeholders
    result.placeholders_found = check_placeholders(content)
    for placeholder in result.placeholders_found:
        result.issues.append({
            "type": "placeholder",
            "detail": placeholder,
            "severity": "high",
        })

    # 3. Check contact info location
    contact_issues = check_contact_info_location(content, content_type, allow_contact)
    if contact_issues:
        result.contact_info_found = True
        for issue in contact_issues:
            result.issues.append({
                "type": "contact_info",
                "detail": issue,
                "severity": "medium",
            })

    return result


def guard_latex(latex_content: str, is_cv: bool = False) -> ContentGuardResult:
    """Convenience wrapper for guarding LaTeX content.

    Args:
        latex_content: The LaTeX content to check.
        is_cv: If True, contact info is expected (CV header).

    Returns:
        ContentGuardResult.
    """
    return guard_content(
        content=latex_content,
        content_type="latex",
        allow_contact=is_cv,
    )


def guard_text(text: str, content_type: str) -> ContentGuardResult:
    """Convenience wrapper for guarding plain text content.

    Args:
        text: The plain text content to check.
        content_type: Type of content ("cover_letter", "email_body", "generic").

    Returns:
        ContentGuardResult.
    """
    return guard_content(
        content=text,
        content_type=content_type,
        allow_contact=(content_type == "cv"),
    )
