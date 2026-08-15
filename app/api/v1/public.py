"""Public (unauthenticated) endpoints.

Anything mounted here must NOT depend on ``get_current_user`` — it is served
to visitors (landing page, /limits) without a token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.billing import ProductCatalogOut
from app.services.plans import NoPlansConfiguredError, build_catalog

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/catalog", response_model=ProductCatalogOut)
async def get_public_catalog(
    db: AsyncSession = Depends(get_db),
) -> ProductCatalogOut:
    """Return the public plans catalog + credit costs for landing / limits.

    Identical shape to the authenticated ``/billing/catalog`` (shared
    ``build_catalog``), minus nothing public: pricing, credits, quotas.
    """
    try:
        return ProductCatalogOut(**await build_catalog(db))
    except NoPlansConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "no_plans_configured", "message": str(exc)}) from exc
