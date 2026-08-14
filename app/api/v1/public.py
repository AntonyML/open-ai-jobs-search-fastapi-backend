"""Public (unauthenticated) endpoints.

Anything mounted here must NOT depend on ``get_current_user`` — it is served
to visitors (landing page, /limits) without a token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.billing import ProductCatalogOut
from app.services.plans import build_catalog

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/catalog", response_model=ProductCatalogOut)
async def get_public_catalog(
    db: AsyncSession = Depends(get_db),
) -> ProductCatalogOut:
    """Return the public plans catalog + credit costs for landing / limits.

    Identical shape to the authenticated ``/billing/catalog`` (shared
    ``build_catalog``), minus nothing public: pricing, credits, quotas.
    """
    return ProductCatalogOut(**await build_catalog(db))
