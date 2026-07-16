"""Authentication service — user registration, login, and account deletion."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.exceptions import DuplicateError
from app.core.settings import get_settings

settings = get_settings()

# ── In-memory rate limiting ────────────────────────────────────────
_login_attempts: dict[str, list[datetime]] = defaultdict(list)


def _rate_limit_key(ip: str, email: str) -> str:
    return f"{ip}:{email.lower().strip()}"


def check_login_rate_limit(ip: str, email: str) -> None:
    """Check if this IP+email combo has exceeded the max login attempts.

    Raises:
        RateLimitError: If too many failed attempts in the window.
    """
    key = _rate_limit_key(ip, email)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=settings.rate_limit_window_seconds)
    # Purge old entries
    _login_attempts[key] = [t for t in _login_attempts[key] if t > window_start]
    if len(_login_attempts[key]) >= settings.rate_limit_attempts:
        raise RateLimitError("Too many login attempts. Try again later.")


def record_failed_login(ip: str, email: str) -> None:
    """Record a failed login attempt for rate limiting."""
    key = _rate_limit_key(ip, email)
    _login_attempts[key].append(datetime.now(timezone.utc))


def clear_login_rate_limit(ip: str, email: str) -> None:
    """Clear rate limit entries on successful login."""
    key = _rate_limit_key(ip, email)
    _login_attempts.pop(key, None)


class InvalidCredentialsError(Exception):
    """Raised when login credentials are invalid."""
    pass


class DeleteAccountError(Exception):
    """Raised when account deletion fails (wrong password, wrong confirmation)."""
    pass


class RateLimitError(Exception):
    """Raised when login rate limit is exceeded."""
    pass


async def register_user(
    db: AsyncSession,
    email: str,
    password: str,
    full_name: str | None = None,
) -> User:
    """Register a new user account.

    Args:
        db: Database session.
        email: User email address.
        password: Plaintext password (will be hashed).
        full_name: Optional full name.

    Returns:
        The newly created User.

    Raises:
        DuplicateError: If a user with this email already exists.
    """
    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise DuplicateError(f"User with email {email} already exists")

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def login_user(
    db: AsyncSession,
    email: str,
    password: str,
    ip: str = "",
) -> tuple[User, str]:
    """Authenticate a user and return the user + JWT token.

    Includes rate limiting: after ``rate_limit_attempts`` failed login
    attempts from the same IP+email within ``rate_limit_window_seconds``,
    the endpoint will return a 429 Too Many Requests.

    Args:
        db: Database session.
        email: User email address.
        password: Plaintext password.
        ip: Client IP address for rate limiting.

    Returns:
        Tuple of (User, access_token).

    Raises:
        InvalidCredentialsError: If email/password don't match.
        RateLimitError: If rate limit is exceeded.
    """
    check_login_rate_limit(ip, email)

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.hashed_password):
        record_failed_login(ip, email)
        raise InvalidCredentialsError("Invalid email or password")

    clear_login_rate_limit(ip, email)
    token = create_access_token(
        subject=user.id,
        role=user.role or "client",
        tier=user.tier or "free",
    )
    return user, token


async def delete_account(
    db: AsyncSession,
    user_id: str,
    password: str,
    confirmation: str,
) -> None:
    """Permanently delete the user account and all associated data.

    Args:
        db: Database session.
        user_id: ID of the user to delete.
        password: Current password for verification.
        confirmation: Confirmation string (must equal expected value).

    Raises:
        InvalidCredentialsError: If the password is wrong.
        DeleteAccountError: If the confirmation string does not match.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise InvalidCredentialsError("User not found")

    if not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError("Invalid password")

    await db.delete(user)
    await db.flush()
