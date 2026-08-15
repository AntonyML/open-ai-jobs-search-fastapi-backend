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


# ── Credit-cost calibration (plan.md §8.2) ───────────────────────────
# The canonical action catalog lives in ``app/services/credit_costs.py``;
# these schemas only shape the admin API (GET rich catalog, PUT strict).
# The old ``CreditAction`` enum was removed — it was dead documentation
# that drifted from reality (0 usages; see plan.md §8.1 F1).


class CreditCostOut(BaseModel):
    """Admin view of one billable action: catalog metadata + effective cost."""

    key: str
    group: str
    cost: int
    default_cost: int
    feature_gate: str | None = None
    version: int = 1


class CreditCostsOut(BaseModel):
    """GET /admin/credit-costs — the frontend renders from this response
    (never hardcoded lists, plan.md §8.2)."""

    groups: list[str]
    actions: list[CreditCostOut]


class CreditCostsUpdate(BaseModel):
    """PUT /admin/credit-costs — strict calibration payload.

    ``expected_versions`` enables optimistic locking (HTTP 409 when another
    admin edited first); keys without a version are not conflict-checked.
    """

    costs: dict[str, int]
    expected_versions: dict[str, int] | None = None


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
    refill_weekday: int = 0
    daily_quota: int = 0
    weekly_quota: int = 0
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
    price_monthly_usd: float = Field(0.0, ge=0)
    price_yearly_usd: float = Field(0.0, ge=0)
    credits_per_period: int = Field(0, ge=0)
    refill_cadence: str = "period"
    refill_weekday: int = 0
    daily_quota: int = Field(0, ge=0)
    weekly_quota: int = Field(0, ge=0)
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
    # When the quota windows reset (drives the weekly quota bar, plan.md §4).
    next_reset_at: datetime | None = None
    features: list[str] = []
    credits: list[CreditTransactionOut] = []
    correlation_id: str | None = None


# ── Catalog / config ─────────────────────────────────────────────────


class CreditCosts(BaseModel):
    """Cost (in credits) of each AI action — editable by the admin."""

    cv_base: int = 1
    cv_adapted: int = 1
    rank: int = 1
    expand: int = 1
    upskill: int = 1
    apply: int = 1
    interview: int = 1


class TopupPack(BaseModel):
    """One fixed prepaid credit pack (manual SINPE/WhatsApp payment)."""

    price_usd: float
    credits: int


class ProductCatalogOut(BaseModel):
    """What the frontend needs to render the buy/upgrade UI."""

    plans: list[PlanOut]
    credit_costs: CreditCosts
    topup_packs: list[TopupPack] = []
    whatsapp_number: str = ""
    currency: str = "USD"
    last_updated: datetime | None = None


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


class TopupRequest(BaseModel):
    """A user-initiated top-up request for a fixed credit pack.

    Same manual flow as purchases: the request is recorded + emailed to the
    admin, the user pays via SINPE/WhatsApp, and the admin approves it from
    the admin panel (``POST /admin/credits/topup``).
    """

    # Identifies the pack by its credit amount (50 or 120) — the natural
    # key of ``topup_packs`` since prices are admin-tunable.
    pack_credits: int = Field(..., gt=0, description="Pack identifier: credits included")
    method: PurchaseMethod = PurchaseMethod.SINPE
    phone: str | None = Field(None, description="Phone for SINPE contact")
    note: str | None = None


class TopupRequestOut(BaseModel):
    """Response confirming the top-up request was received."""

    ok: bool = True
    correlation_id: str
    message: str
    whatsapp_number: str = ""
    pack: TopupPack | None = None


# ── Admin adjust ─────────────────────────────────────────────────────


class AdminCreditAdjust(BaseModel):
    """Admin adds/removes credits from a user's account."""

    user_id: str
    delta: int = Field(..., description="Positive to add, negative to remove")
    reason: str | None = None


class AdminTopupApprove(BaseModel):
    """Admin approves a pending top-up: add the pack's credits to the user.

    ``correlation_id`` links the ledger entry back to the ``topup_request``
    notification the user created (optional — the admin can also approve
    ad-hoc).

    plan.md §2.8: ``price_paid`` is **required** — the admin confirms the
    amount actually received (prefilled with the pack price, editable)
    before credits are granted.
    """

    user_id: str
    pack_credits: int = Field(..., gt=0)
    price_paid: float = Field(
        ..., gt=0, description="Amount the user actually paid (USD)"
    )
    correlation_id: str | None = None


class AdminRefundApprove(BaseModel):
    """Admin approves a pending refund: zero-out credits + mark refunded."""

    user_id: str
    correlation_id: str | None = None


# ── Cancel / refund requests (user-facing) ──────────────────────────


class CancelSubscriptionOut(BaseModel):
    """Response to a user cancellation request."""

    ok: bool = True
    message: str
    period_end: datetime | None = None


class RefundRequestOut(BaseModel):
    """Response confirming the refund request was received (policy-gated)."""

    ok: bool = True
    correlation_id: str
    message: str
    whatsapp_number: str = ""


class UpgradeRequest(BaseModel):
    """A user-initiated prorated upgrade request (mid-period plan change).

    Same manual flow as purchases: the request records the prorated
    ``amount_due`` and notifies the admin; once the user pays, the admin
    activates the new plan (with ``price_paid`` = the amount due).
    """

    plan_key: str = Field(..., description="Target plan key, e.g. 'max'")
    method: PurchaseMethod = PurchaseMethod.SINPE
    phone: str | None = Field(None, description="Phone for SINPE contact")
    note: str | None = None


class UpgradeRequestOut(BaseModel):
    """Response confirming the prorated upgrade request was received."""

    ok: bool = True
    correlation_id: str
    message: str
    amount_due: float
    whatsapp_number: str = ""


class AdminSubscriptionCreate(BaseModel):
    """Admin manually activates a subscription for a user."""

    user_id: str
    plan_key: str
    billing_cycle: str = "monthly"  # monthly | yearly
    auto_renew: bool = False
    # What the user actually paid (e.g. the prorated amount of an upgrade).
    price_paid: float = Field(0.0, ge=0)
    note: str | None = None
