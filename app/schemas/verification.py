"""Pydantic schemas for the verification checklist.

FASE 2 — Verification Checklist runs after PDF compilation to ensure
generated documents meet quality standards before being sent to recruiters.

The checklist combines:
- 9 deterministic checks (no LLM calls): name, email, role, company,
  date format, placeholders, ATS parseability, keywords
- 1 LLM check: content quality (fabricated claims, tone consistency,
  role specificity)

100% non-blocking — the pipeline always completes regardless of results.
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ── LLM Content Check output schema ─────────────────────────────────
# Must be a Pydantic model class because the adapter uses
# output_schema.model_json_schema() and output_schema.model_validate().


class LlmContentCheckOutput(BaseModel):
    """Structured output from the LLM content quality check.

    The LLM evaluates the generated documents for fabricated claims,
    profile specificity, and tone consistency — all in one call.
    """

    overall_assessment: str = "pass"  # "pass" | "fail" | "warn"
    fabricated_claims: list[str] = Field(
        default_factory=list,
        description="Specific fabricated claims found (empty if none)",
    )
    profile_specific: bool = False
    tone_consistent: bool = False
    issues: list[str] = Field(
        default_factory=list,
        description="Issue descriptions (max 3)",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Improvement suggestions (max 3)",
    )


class VerificationCheck(BaseModel):
    """A single check in the verification checklist."""

    name: str = Field(..., description="Short identifier for the check")
    label: str = Field(..., description="Human-readable check name")
    category: str = Field(
        ...,
        description="One of: content, formatting, ats, llm",
    )
    passed: bool = Field(..., description="Whether this check passed")
    details: str | None = Field(
        None, description="Explanation of what was checked and result"
    )
    suggestion: str | None = Field(
        None, description="How to fix if failed (actionable)"
    )


class VerificationResult(BaseModel):
    """Complete result of running the verification checklist.

    Returned by POST /api/v1/apply/{id}/verify and stored in the
    Application.verification_result JSONB column.

    Attributes:
        application_id: The application that was verified.
        checks: Ordered list of all checks run.
        overall_pass: True only when ALL checks pass.
        passes: List of check names that passed.
        failures: List of check names that failed.
        warnings: List of check names that passed with caveats.
        ats_score: Overall ATS keyword coverage (0.0-1.0), if available.
        summary: Human-readable one-line summary.
        checked_at: When the verification was performed.
    """

    application_id: str
    checks: list[VerificationCheck] = Field(
        default_factory=list, max_length=20
    )
    overall_pass: bool = False
    passes: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ats_score: float | None = Field(None, ge=0.0, le=1.0)
    summary: str = ""
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class VerificationResponse(BaseModel):
    """API response for the verification endpoint."""

    application_id: str
    result: VerificationResult
    message: str
