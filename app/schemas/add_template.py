"""Pydantic schemas for the add-template skill.

Request/response shapes for registering and switching custom templates.
"""

from datetime import datetime
from pydantic import BaseModel, Field


# ── Request schemas ─────────────────────────────────────────────────


class AddTemplateRequest(BaseModel):
    """Trigger registration of a custom CV or Cover Letter LaTeX template."""

    name: str = Field(
        ...,
        description="Short kebab-case identifier (e.g., 'awesome-cv', 'classic-serif')",
    )
    template_type: str = Field(
        ...,
        description="Type of the template. Must be 'cv' or 'cover_letter'",
    )
    latex_content: str = Field(
        ...,
        description="LaTeX template skeleton content containing placeholder tokens like [YOUR_NAME]",
    )
    engine: str = Field(
        "lualatex",
        description="Compile engine: lualatex, xelatex, or pdflatex",
    )
    fonts: str = Field(
        ...,
        description="Font summary description, including any path notes for bundled fonts",
    )
    style_rules: list[str] = Field(
        default_factory=list,
        description="Style rules to preserve when tailoring this template",
    )
    page_limit: int = Field(
        ...,
        description="Hard page count for the compiled PDF (e.g., 2 for CV, 1 for cover letter)",
    )
    known_pitfalls: str | None = Field(
        None,
        description="Macros that break with certain content or characters that need escaping",
    )


class SwitchTemplateRequest(BaseModel):
    """Payload to activate/switch to a registered template or go back to default."""

    name: str = Field(
        ...,
        description="The kebab-case name of the template to activate, or 'default' to restore stock template",
    )
    template_type: str = Field(
        ...,
        description="Type of template: 'cv' or 'cover_letter'",
    )


# ── Response schemas ────────────────────────────────────────────────


class TemplateOut(BaseModel):
    """Metadata of a registered template."""

    name: str
    template_type: str
    engine: str
    fonts: str
    style_rules: list[str]
    page_limit: int
    known_pitfalls: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime


class TemplateSummaryOut(BaseModel):
    """Lightweight template info for list views."""

    name: str
    template_type: str
    engine: str
    fonts: str
    active: bool
    created_at: datetime
