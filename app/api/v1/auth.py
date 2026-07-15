"""Authentication router — register and login endpoints. Public endpoints (no auth required)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_locale
from app.schemas.auth import Token, UserLogin, UserOut, UserRegister
from app.services import auth
from app.core.i18n.locale import t

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
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """Login with email and password. Returns a JWT access token."""
    try:
        user, token = await auth.login_user(
            db=db,
            email=payload.email,
            password=payload.password,
        )
    except auth.InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("auth.invalid_credentials", locale),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return Token(access_token=token)
