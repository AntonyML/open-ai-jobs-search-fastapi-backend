"""Pydantic schemas for plans, subscriptions, credits and purchases."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ── Enums ────────────────────────────────────────────────────────────


class PurchaseMethod(str, Enum):
    """Manual payment channels (Costa Rica)."""

    SINPE = "sinpe"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class CreditAction(str, Enum):
    """Credit ledger actions."""

    REFILL = "refill"
    SIGNUP_BONUS = "signup_bonus"
    PURCHASE = "purchase"
    ADMIN_ADJUST = "admin_adjust"
    CV_BASE = "cv_base"
    CV_ADAPTED = "cv_adapted"
    PIPELINE = "pipeline"
    EXPIRY = "expiry"


# ── Plans ────────────────────────────────────────────────────────────


class PlanOut(BaseModel):
    """Public view of a plan (catalog / pricing)."""

    key: str
    name: str
    description: str | None = None
    price_monthly_usd: float
    price_yearly_usd: float
    credits_per_period: int
    refill_cadence: str
    features: list[str] = []
    is_active: bool = True
    sort_order: int = 10

    model_config = {"from_attributes": True}


class PlanAdminOut(PlanOut):
    """Admin view of a plan — includes quotas."""

    id: str
    refill_weekday: int
    daily_quota: int
    weekly_quota: int


class PlanUpsert(BaseModel):
    """Admin create/update payload for a plan."""

    key: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    price_monthly_usd: float = 0.0
    price_yearly_usd: float = 0.0
    credits_per_period: int = 0
    refill_cadence: str = "period"
    refill_weekday: int = 0
    daily_quota: int = 0
    weekly_quota: int = 0
    features: list[str] = []
    is_active: bool = True
    sort_order: int = 10


# ── Subscriptions ────────────────────────────────────────────────────


class UserSubscriptionOut(BaseModel):
    """Subscription summary for a user."""

    id: str
    plan_key: str
    correlation_id: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    status: str
    source: str
    auto_renew: bool
    price_paid: float
    is_expired: bool = False

    model_config = {"from_attributes": True}


class SubscriptionAdminOut(UserSubscriptionOut):
    """Admin view of a subscription (includes user identity)."""

    user_id: str
    user_email: str = ""


# ── Credits ──────────────────────────────────────────────────────────


class CreditTransactionOut(BaseModel):
    """One immutable credit ledger entry."""

    id: str
    action: str
    credits_delta: int
    description: str | None = None
    model_used: str | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class CreditStatusOut(BaseModel):
    """Full billing/credit status for the authenticated user."""

    tier: str
    plan_key: str | None = None
    plan_name: str | None = None
    has_active_subscription: bool
    subscription: UserSubscriptionOut | None = None
    credits_balance: int
    credits_total: int
    credits_used: int
    period_start: datetime | None = None
    period_end: datetime | None = None
    quota_day_used: int
    quota_day_limit: int
    quota_week_used: int
    quota_week_limit: int
    features: list[str] = []
    credits: list[CreditTransactionOut] = []
    correlation_id: str | None = None


# ── Catalog / config ─────────────────────────────────────────────────


class CreditCosts(BaseModel):
    """Cost (in credits) of each AI action — editable by the admin."""

    cv_base: int = 1
    cv_adapted: int = 1
    pipeline: int = 1


class ProductCatalogOut(BaseModel):
    """What the frontend needs to render the buy/upgrade UI."""

    plans: list[PlanOut]
    credit_costs: CreditCosts
    whatsapp_number: str = ""
    currency: str = "USD"


# ── Purchase requests (manual SINPE / WhatsApp) ──────────────────────


class PurchaseRequest(BaseModel):
    """A user-initiated purchase request for a plan or credit package.

    Fully manual flow: the request is recorded + emailed to the admin,
    the user pays via SINPE/WhatsApp, and the admin activates the
    subscription from the admin panel.
    """

    plan_key: str = Field(..., description="Target plan key, e.g. 'pro' or 'max'")
    method: PurchaseMethod = PurchaseMethod.SINPE
    phone: str | None = Field(None, description="Phone for SINPE contact")
    note: str | None = None
    # Billing period chosen by the user: 'monthly' | 'yearly'
    billing_cycle: str = "monthly"


class PurchaseRequestOut(BaseModel):
    """Response confirming the purchase request was received."""

    ok: bool = True
    correlation_id: str
    message: str
    whatsapp_number: str = ""


# ── Admin adjust ─────────────────────────────────────────────────────


class AdminCreditAdjust(BaseModel):
    """Admin adds/removes credits from a user's account."""

    user_id: str
    delta: int = Field(..., description="Positive to add, negative to remove")
    reason: str | None = None


class AdminSubscriptionCreate(BaseModel):
    """Admin manually activates a subscription for a user."""

    user_id: str
    plan_key: str
    billing_cycle: str = "monthly"  # monthly | yearly
    auto_renew: bool = False
    note: str | None = None
