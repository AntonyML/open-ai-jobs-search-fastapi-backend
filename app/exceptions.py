"""Business exceptions and their FastAPI exception handlers.

Every service raises these instead of sprinkling HTTPException throughout
the codebase.  Handlers are registered on the app in main.py.
"""

from fastapi import Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base exception for all business-logic errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str = "An unexpected error occurred"):
        self.message = message


class NotFoundError(AppError):
    """A requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ProviderAuthError(AppError):
    """The user has not configured API credentials for the requested LLM provider."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "provider_not_configured"


class ProfileIncompleteError(AppError):
    """The candidate profile is missing required fields for the operation."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "profile_incomplete"


class ScraperError(AppError):
    """A scraper subprocess exited with a non-zero code."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "scraper_failed"


class LLMError(AppError):
    """The LLM call failed (timeout, rate-limit, invalid response)."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "llm_error"


class LatexCompileError(AppError):
    """LaTeX compilation failed or produced wrong page count."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "latex_compile_error"


class DuplicateError(AppError):
    """Attempted to create a resource that already exists."""

    status_code = status.HTTP_409_CONFLICT
    code = "duplicate"


class ConfirmationRequiredError(AppError):
    """A destructive action was attempted without the required explicit confirmation."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "confirmation_required"


# ── Exception handlers (registered in create_app) ────────────────


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message},
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch Pydantic validation errors from FastAPI and normalise the shape."""
    from fastapi.exceptions import RequestValidationError

    if isinstance(exc, RequestValidationError):
        details = exc.errors()
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": "validation_error",
                "message": "Request validation failed",
                "details": details,
            },
        )
    # Fallback — should not happen if registered correctly
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal_error", "message": "Unexpected validation error"},
    )