"""Business exceptions and their FastAPI exception handlers.

Every service raises these instead of sprinkling HTTPException throughout
the codebase.  Handlers are registered on the app in main.py.
"""

from fastapi import Request, status
from fastapi.responses import JSONResponse


from app.core.i18n.locale import t as _t


class AppError(Exception):
    """Base exception for all business-logic errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    locale: str = "en"

    def __init__(self, message: str | None = None, locale: str = "en"):
        self.locale = locale
        self.message = message or _t("errors.internal", locale)


class NotFoundError(AppError):
    """A requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"

    def __init__(self, message: str | None = None, locale: str = "en"):
        super().__init__(message or _t("errors.not_found", locale), locale=locale)


class ProviderAuthError(AppError):
    """The user has not configured API credentials for the requested LLM provider."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "provider_not_configured"

    def __init__(self, message: str | None = None, locale: str = "en"):
        super().__init__(message or _t("errors.provider_not_configured", locale), locale=locale)


class ProfileIncompleteError(AppError):
    """The candidate profile is missing required fields for the operation."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "profile_incomplete"

    def __init__(self, message: str | None = None, locale: str = "en"):
        super().__init__(message or _t("errors.profile_incomplete", locale), locale=locale)


class LLMError(AppError):
    """The LLM call failed (timeout, rate-limit, invalid response)."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "llm_error"

    def __init__(self, message: str | None = None, locale: str = "en"):
        super().__init__(message or _t("errors.llm_error", locale), locale=locale)


class DuplicateError(AppError):
    """Attempted to create a resource that already exists."""

    status_code = status.HTTP_409_CONFLICT
    code = "duplicate"

    def __init__(self, message: str | None = None, locale: str = "en"):
        super().__init__(message or _t("errors.duplicate", locale), locale=locale)


class ConfirmationRequiredError(AppError):
    """A destructive action was attempted without the required explicit confirmation."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "confirmation_required"

    def __init__(self, message: str | None = None, locale: str = "en"):
        super().__init__(message or _t("errors.confirmation_required", locale), locale=locale)


# ── Exception handlers (registered in create_app) ────────────────


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    origin = request.headers.get("origin", "")
    from app.core.settings import get_settings
    settings = get_settings()
    allow_origin = origin if origin in settings.cors_origins else (settings.cors_origins[0] if settings.cors_origins else "")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message},
        headers={
            "Access-Control-Allow-Origin": allow_origin,
            "Access-Control-Allow-Credentials": "true",
        },
    )


def _json_safe(obj):
    """Recursively convert bytes values to str so the payload is JSON serializable.

    FastAPI's RequestValidationError may include the raw request body (bytes)
    in its ``input`` field when the request is form-encoded or otherwise not
    JSON, which makes json.dumps raise ``TypeError: Object of type bytes is
    not JSON serializable``.  This walks the structure and decodes any bytes
    it finds.
    """
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch Pydantic validation errors from FastAPI and normalise the shape."""
    from app.core.i18n.locale import get_locale_from_request, t

    from fastapi.exceptions import RequestValidationError

    if isinstance(exc, RequestValidationError):
        locale = get_locale_from_request(request)
        details = _json_safe(exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "validation_error",
                "message": t("errors.validation", locale, detail="Request validation failed"),
                "details": details,
            },
        )
    # Fallback — should not happen if registered correctly
    from app.core.i18n.locale import get_locale_from_request, t
    locale = get_locale_from_request(request)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal_error", "message": t("errors.internal", locale)},
    )