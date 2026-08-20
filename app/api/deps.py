"""Centralised FastAPI dependencies.

Every router imports Depends from here instead of repeating
get_db / get_current_user / get_llm_provider inline.
"""

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n.locale import get_locale_from_request
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db as _get_db
from app.services.provider_config import get_active_provider_config

# Re-export the DB session dependency so routers can do
#   from app.api.deps import get_db
get_db = _get_db


async def get_current_user(
    authorization: str | None = Header(None, description="Bearer <token>"),
) -> dict:
    """Validate JWT and return the payload {sub, exp, ...}.

    Attach to any endpoint that requires authentication:
        user: dict = Depends(get_current_user)

    NOTE: Header uses ``None`` default instead of ``...`` so that missing
    auth returns 401 (Unauthorized) instead of 422 (Unprocessable).
    """
    if not authorization or not authorization.startswith("Bearer "):
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
        ) from None
    return payload


# ── LLM provider dependency ──────────────────────────────────────────
async def get_llm_provider(
    db: AsyncSession = Depends(_get_db),
) -> dict:
    """Return the global LLM provider config.

    Reads the admin-managed global provider config from the DB.  Falls back
    to settings (.env) if the global config is empty.
    """
    return await get_active_provider_config(db)


# ── Pipeline access (max plan only) ─────────────────────────────────
async def require_max_or_admin(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
) -> dict:
    """Gate pipeline endpoints to ``max`` plan users (or the admin).

    The DB is the source of truth: the JWT ``tier`` is only a first line of
    defence and can be stale in either direction (a Max user right after
    purchase, or a Free user after an expired subscription).  The single
    indexed PK lookup keeps this cheap; the admin is always allowed.
    """
    if user.get("role") == "admin":
        return user
    result = await db.execute(select(User.tier).where(User.id == user["sub"]))
    db_tier = result.scalar_one_or_none()
    if db_tier != "max":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "El pipeline de búsqueda de empleo está disponible solo en el plan Max. "
                "Adquiere el plan Max para desbloquear ofertas, rankings, postulaciones, "
                "entrevistas, skills y plan de formación."
            ),
        )
    user["tier"] = "max"
    return user


# ── Locale dependency ───────────────────────────────────────────────
async def get_locale(request: Request) -> str:
    """Extract the user's preferred locale from the request.

    Checks:
    1. The ``locale`` cookie (set by the frontend next-intl middleware)
    2. The ``Accept-Language`` header
    3. Falls back to the default (``en``)

    Usage::

        from app.api.deps import get_locale
        from app.core.i18n.locale import t

        @router.get("/example")
        async def example(locale: str = Depends(get_locale)):
            return {"message": t("common.saved", locale)}
    """
    return get_locale_from_request(request)
