"""Setup router — candidate profile onboarding endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_locale
from app.core.i18n.locale import t
from app.db.session import get_db as _get_db
from app.exceptions import NotFoundError
from app.schemas.profile import (
    BehavioralProfileCreate,
    BehavioralProfileOut,
    CandidateProfileCreate,
    CandidateProfileOut,
    CandidateProfileUpdate,
    StarExampleCreate,
    StarExampleOut,
)
from app.services import setup

router = APIRouter(prefix="/setup", tags=["setup"])


# ── Candidate Profile ───────────────────────────────────────────────


@router.post(
    "/profile",
    response_model=CandidateProfileOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    payload: CandidateProfileCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
    locale: str = Depends(get_locale),
):
    """Create a new candidate profile."""
    data = payload.model_dump(exclude_none=True)
    profile = await setup.create_profile(db, user["sub"], data)
    await db.commit()
    await db.refresh(profile, ["user"])
    return profile


@router.get("/profile", response_model=CandidateProfileOut | None)
async def get_profile(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Retrieve the authenticated user's candidate profile."""
    try:
        profile = await setup.get_profile(db, user["sub"])
        return profile
    except NotFoundError:
        return None


@router.patch("/profile", response_model=CandidateProfileOut)
async def update_profile(
    payload: CandidateProfileUpdate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
    locale: str = Depends(get_locale),
):
    """Partially update the candidate profile."""
    data = payload.model_dump(exclude_unset=True)
    profile = await setup.update_profile(db, user["sub"], data)
    await db.commit()
    await db.refresh(profile, ["user"])
    return profile


@router.delete("/profile", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete the candidate profile and all related data."""
    await setup.delete_profile(db, user["sub"])
    await db.commit()


@router.post("/profile/complete", response_model=CandidateProfileOut)
async def complete_setup(
    setup_method: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
    locale: str = Depends(get_locale),
):
    """Mark the profile setup as completed."""
    if setup_method not in ("documents", "cv_import", "interview"):
        from app.exceptions import AppError

        raise AppError(t("errors.validation", locale, detail="Invalid setup_method"))
    profile = await setup.complete_setup(db, user["sub"], setup_method)
    await db.commit()
    await db.refresh(profile, ["user"])
    return profile


# ── Behavioral Profile ──────────────────────────────────────────────


@router.get(
    "/behavioral-profile",
    response_model=BehavioralProfileOut | None,
)
async def get_behavioral_profile(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Retrieve the behavioral profile for the authenticated user.

    Returns null (not 404) when no profile has been created yet.
    """
    try:
        candidate = await setup.get_profile(db, user["sub"])
        return await setup.get_behavioral_profile(db, candidate.id)
    except NotFoundError:
        return None


@router.put(
    "/behavioral-profile",
    response_model=BehavioralProfileOut,
)
async def upsert_behavioral_profile(
    payload: BehavioralProfileCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Create or update the behavioral profile."""
    candidate = await setup.get_profile(db, user["sub"])
    data = payload.model_dump(exclude_none=True)
    bp = await setup.upsert_behavioral_profile(db, candidate.id, data)
    await db.commit()
    await db.refresh(bp)
    return bp


# ── STAR Examples ──────────────────────────────────────────────────


@router.get(
    "/star-examples",
    response_model=list[StarExampleOut],
)
async def list_star_examples(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all STAR examples for the authenticated user."""
    candidate = await setup.get_profile(db, user["sub"])
    return await setup.list_star_examples(db, candidate.id)


@router.post(
    "/star-examples",
    response_model=StarExampleOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_star_example(
    payload: StarExampleCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Create a new STAR example."""
    candidate = await setup.get_profile(db, user["sub"])
    data = payload.model_dump(exclude_none=True)
    example = await setup.create_star_example(db, candidate.id, data)
    await db.commit()
    await db.refresh(example)
    return example


@router.delete(
    "/star-examples/{example_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_star_example(
    example_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete a STAR example."""
    candidate = await setup.get_profile(db, user["sub"])
    await setup.delete_star_example(db, example_id, candidate.id)
    await db.commit()
