"""Centralised FastAPI dependencies.

Every router imports Depends from here instead of repeating
get_db / get_current_user / get_llm_provider inline.
"""

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db as _get_db
from app.services.provider_credentials import get_user_active_provider_config

# Re-export the DB session dependency so routers can do
#   from app.api.deps import get_db
get_db = _get_db


async def get_current_user(
    authorization: str = Header(..., description="Bearer <token>"),
) -> dict:
    """Validate JWT and return the payload {sub, exp, ...}.

    Attach to any endpoint that requires authentication:
        user: dict = Depends(get_current_user)
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return payload


# ── LLM provider dependency ──────────────────────────────────────────
async def get_llm_provider(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
) -> dict:
    """Return the LLM provider config for the authenticated user.

    Queries the user's active provider and stored credentials from the DB.
    Falls back to settings if no credential is stored.
    """
    return await get_user_active_provider_config(db, user["sub"])