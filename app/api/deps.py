"""Centralised FastAPI dependencies.

Every router imports Depends from here instead of repeating
get_db / get_current_user / get_llm_provider inline.
"""

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db as _get_db

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


# ── LLM provider dependency (populated in Fase 3 when user profiles exist) ──
# Placeholder — will read the user's active provider from the DB.
async def get_llm_provider(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
) -> dict:
    """Return the LLM provider config for the authenticated user.

    Currently a stub; will query provider_credentials in Fase 3.
    """
    return {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "api_key": None,  # resolved from settings for now
        "api_base": None,
    }