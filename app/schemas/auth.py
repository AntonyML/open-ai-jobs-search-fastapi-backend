"""Pydantic schemas for authentication (register/login)."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ── Request schemas ─────────────────────────────────────────────────
class UserRegister(BaseModel):
    """Register a new user account."""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")
    full_name: str | None = Field(None, description="Optional full name")


class UserLogin(BaseModel):
    """Login with existing credentials."""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., description="User password")


class DeleteAccountRequest(BaseModel):
    """Request to delete the user's account."""

    password: str = Field(..., description="Current password for confirmation")
    confirmation: str = Field(..., min_length=1, description='Type "CONFIRMAR" to proceed')


# ── Response schemas ────────────────────────────────────────────────
class Token(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """Decoded JWT payload."""

    sub: str
    exp: datetime


class UserOut(BaseModel):
    """Public user information."""

    id: str
    email: str
    full_name: str | None = None
    is_active: bool = True
    role: str = "client"
    tier: str = "free"
    active_provider: str = "anthropic"
    has_profile: bool = False
    model_config = {"from_attributes": True}


# ── Admin schemas ─────────────────────────────────────────────────
class AdminUserUpdate(BaseModel):
    """Admin updates a user's tier or role."""

    tier: str | None = None
    role: str | None = None


class AdminUserOut(BaseModel):
    """Admin view of a user — includes all info."""

    id: str
    email: str
    full_name: str | None = None
    is_active: bool = True
    role: str = "client"
    tier: str = "free"
    active_provider: str = "anthropic"
    created_at: datetime | None = None
    model_config = {"from_attributes": True}


class AdminUserListOut(BaseModel):
    """Paginated admin user list with global stats.

    The backend owns filtering, sorting and pagination so the admin table
    only ever loads the current page (default 5 rows) instead of every user.
    """

    items: list[AdminUserOut]
    total: int
    page: int
    page_size: int
    # plan.md §2.7: the legacy ``premium`` counter was almost always 0; it is
    # replaced by ``active_subs`` (users with an active subscription).
    stats: dict[str, int] = {"total": 0, "admins": 0, "active_subs": 0}


# ── Payment / Upgrade schemas ─────────────────────────────────────
class UpgradeRequest(BaseModel):
    """User upgrade / payment request."""

    method: str = "email"  # "sinpe" or "email"
    phone: str | None = None  # Costa Rica phone (required for SINPE)


class DonationRequest(BaseModel):
    """Donation notification."""

    amount: str = ""
    method: str = "email"  # "sinpe" or "email"
    phone: str | None = None
