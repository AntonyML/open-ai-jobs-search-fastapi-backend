"""Pydantic schemas for the reset skill.

Request/response shapes for candidate profile and documents resetting.
"""

from pydantic import BaseModel, Field


class ResetRequest(BaseModel):
    """Trigger reset of candidate profile data and/or career documents."""

    scope: str = Field(
        ...,
        description="The scope to reset. Must be one of: 'profile', 'documents', or 'all'",
    )
    confirm: str | None = Field(
        None,
        description="Destructive confirmation token. Must be exactly 'RESET' to execute.",
    )


class ResetResponse(BaseModel):
    """Result of a reset operation."""

    status: str
    scope: str
    cleared: list[str]
    unchanged: list[str]
    message: str
