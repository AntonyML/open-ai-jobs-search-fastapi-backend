"""Security utilities: JWT creation/verification, password hashing, API key encryption."""

import base64
from datetime import UTC, datetime, timedelta

import bcrypt
from cryptography.fernet import Fernet
from jose import jwt

from app.core.settings import get_settings

settings = get_settings()

# Fernet key for API key encryption (derived from JWT secret)
# In production, use a separate dedicated key
_fernet_key = base64.urlsafe_b64encode(settings.jwt_secret_key.encode()[:32].ljust(32, b"0"))
_fernet = Fernet(_fernet_key)


def hash_password(password: str) -> str:
    """Return bcrypt hash of the plaintext password."""
    # Use bcrypt directly.  passlib 1.7.4 runs a >72-byte backend probe that
    # raises with newer Python/bcrypt combinations, making every login 500.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Treat malformed/unsupported stored hashes as invalid credentials,
        # never as an internal server error from the login endpoint.
        return False


def create_access_token(
    subject: str,
    expires_minutes: int | None = None,
    role: str = "client",
    tier: str = "free",
) -> str:
    """Create a signed JWT for the given subject (user id)."""
    expire = datetime.now(UTC) + timedelta(minutes=expires_minutes or settings.jwt_expire_minutes)
    to_encode = {"sub": subject, "exp": expire, "role": role, "tier": tier}
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key for storage.

    Args:
        api_key: Plaintext API key

    Returns:
        Base64-encoded encrypted key
    """
    return _fernet.encrypt(api_key.encode()).decode()


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt an API key from storage.

    Args:
        encrypted_key: Base64-encoded encrypted key

    Returns:
        Plaintext API key
    """
    return _fernet.decrypt(encrypted_key.encode()).decode()
