"""Pydantic schemas for the CV cutter — relevance-weighted bullet removal.

The CV cutter scores each bullet in the tailored experience section and
removes the lowest-value bullets to keep the CV within page limits.
100% deterministic — no LLM calls.
"""

from pydantic import BaseModel, Field


class ScoredBullet(BaseModel):
    """A single bullet with its relevance score.

    The score (0.0–1.0) determines which bullets get removed first when
    the CV exceeds its page limit. Higher score = more valuable to keep.
    """

    entry_index: int = Field(..., description="Index of the TailoredExperienceEntry")
    bullet_index: int = Field(..., description="Index of this bullet within the entry")
    text: str = Field(..., description="The bullet text content")

    relevance_score: float = Field(0.0, ge=0.0, le=1.0, description="Keyword overlap score")
    uniqueness_score: float = Field(0.0, ge=0.0, le=1.0, description="How unique this bullet is vs others")
    cover_reference_score: float = Field(0.0, ge=0.0, le=1.0, description="Whether cover letter references this bullet")
    combined_score: float = Field(0.0, ge=0.0, le=1.0, description="Weighted composite of the three scores")


class CVTrimResult(BaseModel):
    """Result of trimming a CV to a page limit.

    Reports what was removed and the final state so the caller can
    log or display the trimming decisions.
    """

    entries_before: int = Field(..., description="Number of experience entries before trimming")
    bullets_before: int = Field(..., description="Total bullets before trimming")
    bullets_removed: int = Field(..., description="Number of bullets removed")
    pages_achieved: int = Field(..., description="Final page count after trimming")
    removed_bullet_texts: list[str] = Field(
        default_factory=list,
        description="Text of each removed bullet, for audit trail",
    )
    remaining_bullets_per_entry: list[int] = Field(
        default_factory=list,
        description="Bullet count per entry after trimming",
    )
    was_trimmed: bool = Field(False, description="True if any bullets were removed")
