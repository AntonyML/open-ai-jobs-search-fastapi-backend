"""Locale utilities for the backend.

Provides::

    from app.core.i18n.locale import t, get_locale_from_request

    # In a service
    msg = t("rank.completed", request.state.locale, count=42)

    # In an API endpoint via dependency
    msg = t("errors.not_found", locale)

Simple string interpolation using Python's ``str.format()``.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request
from starlette.datastructures import Headers

from app.core.i18n.messages import get_messages

# ── Locale detection ────────────────────────────────────────────────

SUPPORTED_LOCALES = {"en", "es"}
DEFAULT_LOCALE = "en"

# Regex to extract the first language from Accept-Language.
# Handles quality values: "es-MX,es;q=0.9,en;q=0.8" → "es"
_ACCEPT_LANG_RE = re.compile(r"^([a-z]{2})", re.IGNORECASE)


def detect_locale(headers: Headers | dict[str, str], cookie_locale: str | None = None) -> str:
    """Detect the best locale from a request.

    Priority:
    1. ``NEXT_LOCALE`` cookie (set by next-intl middleware on the frontend)
    2. ``Accept-Language`` header
    3. Default (``en``)
    """
    if cookie_locale and cookie_locale in SUPPORTED_LOCALES:
        return cookie_locale

    accept_language = headers.get("accept-language", "")
    if accept_language:
        match = _ACCEPT_LANG_RE.search(accept_language)
        if match and match.group(1).lower() in SUPPORTED_LOCALES:
            return match.group(1).lower()

    return DEFAULT_LOCALE


def get_locale_from_request(request: Request) -> str:
    """Extract the locale from a FastAPI request.

    Checks cookies first, then falls back to Accept-Language header.
    The frontend sends the locale as a cookie (set by next-intl middleware).
    """
    cookie_locale = request.cookies.get("NEXT_LOCALE")
    return detect_locale(request.headers, cookie_locale)


# ── Translation helper ──────────────────────────────────────────────


def t(key: str, locale: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Translate a key into the given locale.

    Simple key lookup with ``str.format()`` interpolation::

        t("rank.completed", "es", count=42)
        # → "Ranking completado — 42 empleos evaluados"

    Falls back to the key itself if no translation is found.
    """
    messages = get_messages(locale)
    template = messages.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError:
            return template
    return template
