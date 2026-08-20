"""Setup service — candidate profile onboarding.

Implements the three paths from the original /setup command:
A) Documents folder (read CV, LinkedIn, diplomas, references)
B) Single CV import (paste or upload)
C) Interview mode (guided questions)

For now, the service handles CRUD for the profile in Supabase.
LLM-assisted extraction (Path A/B) will be added in a later iteration.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models import (
    BehavioralProfile,
    CandidateProfile,
    StarExample,
    User,
)
from app.exceptions import NotFoundError, ProfileIncompleteError

# ── Candidate Profile CRUD ──────────────────────────────────────────


async def get_profile(db: AsyncSession, user_id: str) -> CandidateProfile:
    """Return the candidate profile for a user, or raise NotFoundError."""
    result = await db.execute(
        select(CandidateProfile).options(joinedload(CandidateProfile.user)).where(CandidateProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Candidate profile not found. Run /setup first.")
    return profile


async def create_profile(db: AsyncSession, user_id: str, data: dict[str, Any]) -> CandidateProfile:
    """Create a new candidate profile for the user.

    Args:
        db: Database session.
        user_id: The authenticated user's ID.
        data: Profile fields (validated by Pydantic schema).

    Returns:
        The newly created CandidateProfile.

    Raises:
        ProfileIncompleteError: If required fields are missing.
    """
    # Single query: load User + check if profile already exists.
    # populate_existing ensures a previously-created profile is re-loaded even
    # when the User instance is already present in the session identity map
    # (e.g. after a prior create_profile call in the same session).
    user_result = await db.execute(
        select(User)
        .options(joinedload(User.candidate_profile))
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
    )
    user = user_result.unique().scalar_one()
    if user.candidate_profile is not None:
        raise ProfileIncompleteError("Profile already exists. Use PATCH to update.")

    # Identity fields (full_name, email) are owned by User — write them there
    identity_fields = {"full_name", "email"}
    identity_data = {k: v for k, v in data.items() if k in identity_fields and v is not None}
    for key, value in identity_data.items():
        setattr(user, key, value)

    profile_data = {k: v for k, v in data.items() if k not in identity_fields}
    profile = CandidateProfile(user_id=user_id, **profile_data)
    db.add(profile)
    # Re-fetch with joined user so .full_name / .email properties work
    await db.flush()
    await db.refresh(profile, ["user"])
    return profile


async def update_profile(db: AsyncSession, user_id: str, data: dict[str, Any]) -> CandidateProfile:
    """Partially update the candidate profile.

    Only the fields present in ``data`` are changed.  ``None`` values
    are treated as "no change" — use an empty list/string to clear a field.
    """
    profile = await get_profile(db, user_id)

    # Identity fields (full_name, email) are owned by User — write them there
    identity_fields = {"full_name", "email"}
    identity_data = {k: v for k, v in data.items() if k in identity_fields and v is not None}
    if identity_data:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
        for key, value in identity_data.items():
            setattr(user, key, value)

    profile_data = {k: v for k, v in data.items() if k not in identity_fields}
    for key, value in profile_data.items():
        if value is not None:
            setattr(profile, key, value)

    await db.flush()
    return profile


async def delete_profile(db: AsyncSession, user_id: str) -> None:
    """Delete the candidate profile and all related data."""
    profile = await get_profile(db, user_id)
    await db.delete(profile)
    await db.flush()


async def complete_setup(db: AsyncSession, user_id: str, setup_method: str) -> CandidateProfile:
    """Mark the profile setup as completed.

    Args:
        db: Database session.
        user_id: The authenticated user's ID.
        setup_method: One of "documents", "cv_import", "interview".

    Returns:
        The updated CandidateProfile.
    """
    from datetime import datetime

    profile = await get_profile(db, user_id)
    profile.setup_method = setup_method
    profile.setup_completed_at = datetime.now(UTC)
    await db.flush()
    return profile


# ── Behavioral Profile CRUD ─────────────────────────────────────────


async def get_behavioral_profile(db: AsyncSession, candidate_id: str) -> BehavioralProfile:
    """Return the behavioral profile for a candidate."""
    result = await db.execute(select(BehavioralProfile).where(BehavioralProfile.candidate_id == candidate_id))
    bp = result.scalar_one_or_none()
    if bp is None:
        raise NotFoundError("Behavioral profile not found.")
    return bp


async def upsert_behavioral_profile(db: AsyncSession, candidate_id: str, data: dict[str, Any]) -> BehavioralProfile:
    """Create or update the behavioral profile for a candidate."""
    try:
        bp = await get_behavioral_profile(db, candidate_id)
        for key, value in data.items():
            if value is not None:
                setattr(bp, key, value)
    except NotFoundError:
        bp = BehavioralProfile(candidate_id=candidate_id, **data)
        db.add(bp)

    await db.flush()
    return bp


# ── STAR Examples CRUD ──────────────────────────────────────────────


async def list_star_examples(db: AsyncSession, candidate_id: str) -> list[StarExample]:
    """Return all STAR examples for a candidate."""
    result = await db.execute(
        select(StarExample).where(StarExample.candidate_id == candidate_id).order_by(StarExample.created_at)
    )
    return list(result.scalars().all())


async def create_star_example(db: AsyncSession, candidate_id: str, data: dict[str, Any]) -> StarExample:
    """Create a new STAR example."""
    example = StarExample(candidate_id=candidate_id, **data)
    db.add(example)
    await db.flush()
    return example


async def delete_star_example(db: AsyncSession, example_id: str, candidate_id: str) -> None:
    """Delete a STAR example, verifying ownership."""
    result = await db.execute(
        select(StarExample).where(
            StarExample.id == example_id,
            StarExample.candidate_id == candidate_id,
        )
    )
    example = result.scalar_one_or_none()
    if example is None:
        raise NotFoundError("STAR example not found.")
    await db.delete(example)
    await db.flush()
