"""Add-template router — endpoints for registering and switching LaTeX templates."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.session import get_db as _get_db
from app.schemas.add_template import (
    AddTemplateRequest,
    SwitchTemplateRequest,
    TemplateOut,
    TemplateSummaryOut,
)
from app.services import add_template

router = APIRouter(prefix="/add-template", tags=["add-template"])


@router.post(
    "/",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_add_template(
    payload: AddTemplateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Register and test-compile a new custom LaTeX template.

    Writes the template skeleton and manifest to the filesystem under external/templates/.
    If the template compiles successfully and fits within the page limit, it is
    automatically activated.
    """
    result = await add_template.execute_add_template(
        db=db,
        user_id=user["sub"],
        payload=payload,
    )
    return result


@router.post("/switch", status_code=status.HTTP_200_OK)
async def switch_active_template(
    payload: SwitchTemplateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Switch the active template for a given type, or revert to default."""
    result = await add_template.execute_switch_template(
        db=db,
        user_id=user["sub"],
        req=payload,
    )
    return result


@router.get("/{template_type}/{name}", response_model=TemplateOut)
async def get_template_details(
    template_type: str,
    name: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Get metadata details of a specific registered template."""
    return await add_template.get_template(
        db=db,
        name=name,
        template_type=template_type,
        user_id=user["sub"],
    )


@router.get("/", response_model=list[TemplateSummaryOut])
async def list_registered_templates(
    template_type: str | None = Query(
        None, description="Filter templates by type: 'cv' or 'cover_letter'"
    ),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """List all registered templates, optionally filtered by type."""
    return await add_template.list_templates(
        db=db,
        template_type=template_type,
        user_id=user["sub"],
    )
