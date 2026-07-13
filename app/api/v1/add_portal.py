"""Add-portal router — endpoints for generating new job portal search skills."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.session import get_db as _get_db
from app.schemas.add_portal import AddPortalRequest, PortalSkillOut, PortalSkillSummaryOut
from app.services import add_portal

router = APIRouter(prefix="/add-portal", tags=["add-portal"])


@router.post(
    "/",
    response_model=PortalSkillOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_add_portal(
    payload: AddPortalRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Generate a new job portal search skill.

    Investigates the portal, generates a Bun/TypeScript CLI skill from the
    canonical template, test-runs it, and registers it.

    Runs synchronously in the request. For production, consider moving
    to a background task for long-running portal investigations.
    """
    result = await add_portal.execute_add_portal(
        db=db,
        user_id=user["sub"],
        payload=payload,
    )
    return result


@router.get("/{skill_name}", response_model=PortalSkillOut)
async def get_portal_skill(
    skill_name: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get a portal skill by name."""
    return await add_portal.get_portal_skill(db, skill_name, user["sub"])


@router.get("/", response_model=list[PortalSkillSummaryOut])
async def list_portal_skills(
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all installed portal skills for the authenticated user."""
    return await add_portal.list_portal_skills(db, user["sub"], limit=limit, offset=offset)