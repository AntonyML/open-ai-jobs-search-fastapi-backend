"""Authentication router — register, login, account deletion, and upgrade requests."""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_locale
from app.core.i18n.locale import t
from app.schemas.auth import (
    DeleteAccountRequest,
    DonationRequest,
    Token,
    UpgradeRequest,
    UserLogin,
    UserOut,
    UserRegister,
)
from app.services import auth

logger = logging.getLogger(__name__)

# ── In-memory rate limiting for upgrade / donate ──
_upgrade_cooldowns: dict[str, float] = {}
_UPGRADE_COOLDOWN_SECONDS = 30


def _check_upgrade_rate_limit(user_id: str) -> None:
    """Raise HTTPException 429 if the user requested upgrade/donate too recently."""
    last = _upgrade_cooldowns.get(user_id)
    if last is not None:
        elapsed = time.time() - last
        if elapsed < _UPGRADE_COOLDOWN_SECONDS:
            retry_after = int(_UPGRADE_COOLDOWN_SECONDS - elapsed) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {retry_after}s before sending another request.",
            )


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: UserRegister,
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Register a new user account. Returns the created user profile."""
    try:
        user = await auth.register_user(
            db=db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
    except auth.DuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=t("auth.email_exists", locale),
        ) from exc
    return user


@router.post("/login", response_model=Token)
async def login(
    payload: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Login with email and password. Returns a JWT access token.

    Rate-limited: after ``rate_limit_attempts`` failed attempts from
    the same IP+email pair, subsequent tries return 429 Too Many Requests.
    """
    client_ip = request.client.host if request.client else "unknown"
    try:
        user, token_str = await auth.login_user(
            db=db,
            email=payload.email,
            password=payload.password,
            ip=client_ip,
        )
    except auth.RateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=t("auth.too_many_attempts", locale),
        ) from exc
    except auth.InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("auth.invalid_credentials", locale),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return Token(access_token=token_str)


@router.get("/me", response_model=UserOut)
async def get_me(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated user's profile."""
    from sqlalchemy import select

    from app.db.models import CandidateProfile, User

    result = await db.execute(select(User).where(User.id == user["sub"]))
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    profile_result = await db.execute(select(CandidateProfile).where(CandidateProfile.user_id == user["sub"]).limit(1))
    has_profile = profile_result.scalar_one_or_none() is not None
    out = UserOut.model_validate(db_user)
    out.has_profile = has_profile
    return out


@router.delete("/account", status_code=status.HTTP_200_OK)
async def delete_account(
    payload: DeleteAccountRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Permanently delete the authenticated user's account and all data."""
    try:
        await auth.delete_account(
            db=db,
            user_id=user["sub"],
            password=payload.password,
            confirmation=payload.confirmation,
        )
    except auth.InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("auth.invalid_credentials", locale),
        ) from exc
    return {"message": t("auth.account_deleted", locale)}


@router.post("/upgrade", status_code=status.HTTP_200_OK)
async def request_upgrade(
    payload: UpgradeRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Request a plan upgrade. Sends an email notification to the admin."""
    _check_upgrade_rate_limit(user["sub"])

    from sqlalchemy import select

    from app.core.settings import get_settings
    from app.db.models import User as UserModel
    from app.services.email import send_upgrade_request

    settings = get_settings()
    result = await db.execute(select(UserModel).where(UserModel.id == user["sub"]))
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        await send_upgrade_request(
            admin_email=settings.admin_email,
            user_email=db_user.email,
            user_name=db_user.full_name or db_user.email,
            method=payload.method,
            phone=payload.phone,
        )
    except Exception:
        logger.exception("Failed to send upgrade notification email")

    _upgrade_cooldowns[user["sub"]] = time.time()
    return {"message": t("upgrade.requestSent")}


@router.post("/donate", status_code=status.HTTP_200_OK)
async def donate(
    payload: DonationRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Send a donation notification to the admin."""
    _check_upgrade_rate_limit(user["sub"])

    from sqlalchemy import select

    from app.core.settings import get_settings
    from app.db.models import User as UserModel
    from app.services.email import send_donation_notification

    settings = get_settings()
    result = await db.execute(select(UserModel).where(UserModel.id == user["sub"]))
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        await send_donation_notification(
            admin_email=settings.admin_email,
            user_email=db_user.email,
            user_name=db_user.full_name or db_user.email,
            amount=payload.amount or "No especificado",
            method=payload.method,
            phone=payload.phone,
        )
    except Exception:
        logger.exception("Failed to send donation notification email")

    _upgrade_cooldowns[user["sub"]] = time.time()
    return {"message": t("upgrade.thankYou")}
