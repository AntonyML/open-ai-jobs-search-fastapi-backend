"""Providers router — global provider status for all authenticated users.

Since the system moved to a single admin-managed global LLM provider, there
are no per-user provider credentials anymore.  This router exposes a
read-only view of the active global provider so any user can see which
provider/model the system is using (no API keys are ever returned).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.providers import AdminProviderConfigOut
from app.services.provider_config import get_global_provider_config_out

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/active", response_model=AdminProviderConfigOut)
async def get_active_global_provider(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdminProviderConfigOut:
    """Return the active global provider configuration (read-only).

    Shows which provider/model the system currently uses for all LLM calls,
    including the last health status.  Never includes the API key.
    """
    config = await get_global_provider_config_out(db)
    return AdminProviderConfigOut(**config)
