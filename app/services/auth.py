"""Authentication service — user registration, login, and account deletion."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.exceptions import DuplicateError


class InvalidCredentialsError(Exception):
    """Raised when login credentials are invalid."""
    pass


class DeleteAccountError(Exception):
    """Raised when account deletion fails (wrong password, wrong confirmation)."""
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
    # Check if user already exists
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


async def login_user(db: AsyncSession, email: str, password: str) -> tuple[User, str]:
    """Authenticate a user and return the user + JWT token.

    Args:
        db: Database session.
        email: User email address.
        password: Plaintext password.

    Returns:
        Tuple of (User, access_token).

    Raises:
        InvalidCredentialsError: If email/password don't match.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError("Invalid email or password")

    token = create_access_token(subject=user.id)
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
