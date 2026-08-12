"""Billing router — credit status, catalog and manual purchase requests.

The payment flow is intentionally manual (Costa Rica): the user requests a
plan, pays via SINPE/WhatsApp, and the admin activates the subscription from
the admin panel.  No payment gateway is involved.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_locale
from app.core.i18n.locale import t
from app.core.settings import get_settings
from app.db.models import AppNotification, User
from app.schemas.billing import (
    CreditStatusOut,
    CreditTransactionOut,
    PlanOut,
    ProductCatalogOut,
    PurchaseRequest,
    PurchaseRequestOut,
    UserSubscriptionOut,
)
from app.services import credits
from app.services.email import send_purchase_request
from app.services.plans import get_active_plans, get_credit_costs, get_plan, get_whatsapp_number
from app.services.subscriptions import (
    ensure_admin_subscription,
    get_user_access,
    process_expired_subscriptions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


async def _notify_admin(db: AsyncSession, type_: str, title: str, body: str | None) -> None:
    """Create an in-app notification for the first admin user."""
    result = await db.execute(
        select(User).where(User.role == "admin").order_by(User.created_at.asc()).limit(1)
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        return
    db.add(
        AppNotification(
            user_id=admin.id,
            type=type_,
            title=title,
            body=body,
        )
    )
    await db.flush()


@router.get("/status", response_model=CreditStatusOut)
async def get_billing_status(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreditStatusOut:
    """Return the user's subscription + credit status.

    Lazily: ensures the admin's auto-renewing subscription, expires stale
    subscriptions, applies due refills and resets quotas.
    """
    uid = user["sub"]
    role = user.get("role", "client")

    if role == "admin":
        await ensure_admin_subscription(db, uid)
    await process_expired_subscriptions(db)

    access = await get_user_access(db, user)
    balance = await credits.get_balance(db, uid)
    txn_rows = await credits.get_recent_transactions(db, uid, limit=20)

    sub = access["subscription"]
    plan = access["plan"]
    return CreditStatusOut(
        tier=user.get("tier", "free"),
        plan_key=sub.plan_key if sub else None,
        plan_name=plan.name if plan else None,
        has_active_subscription=sub is not None,
        subscription=UserSubscriptionOut.model_validate(sub) if sub else None,
        credits_balance=balance["balance"],
        credits_total=balance["total_earned"],
        credits_used=balance["total_used"],
        period_start=sub.period_start if sub else None,
        period_end=sub.period_end if sub else None,
        quota_day_used=balance["quota_day_used"],
        quota_day_limit=access["daily_quota"],
        quota_week_used=balance["quota_week_used"],
        quota_week_limit=access["weekly_quota"],
        features=access["features"],
        credits=[CreditTransactionOut.model_validate(x) for x in txn_rows],
        correlation_id=sub.correlation_id if sub else None,
    )


@router.get("/catalog", response_model=ProductCatalogOut)
async def get_catalog(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductCatalogOut:
    """Return the plans catalog + credit costs for the buy/upgrade UI."""
    plans = await get_active_plans(db)
    return ProductCatalogOut(
        plans=[PlanOut.model_validate(p) for p in plans],
        credit_costs=(await get_credit_costs(db)),
        whatsapp_number=await get_whatsapp_number(),
    )


@router.get("/transactions", response_model=list[CreditTransactionOut])
async def get_transactions(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CreditTransactionOut]:
    """Return the user's recent credit ledger entries."""
    rows = await credits.get_recent_transactions(db, user["sub"], limit=50)
    return [CreditTransactionOut.model_validate(x) for x in rows]


@router.post("/purchase", response_model=PurchaseRequestOut)
async def request_purchase(
    payload: PurchaseRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
) -> PurchaseRequestOut:
    """Record a manual purchase request (SINPE / WhatsApp / email).

    The admin receives an in-app notification + email, contacts the user,
    and activates the subscription from the admin panel.
    """
    plan = await get_plan(db, payload.plan_key)
    if plan is None or not plan.is_active:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Unknown or inactive plan",
        )

    correlation_id = uuid.uuid4().hex
    result = await db.execute(select(User).where(User.id == user["sub"]))
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="User not found")

    await _notify_admin(
        db,
        "purchase_request",
        f"Solicitud de compra: {plan.name}",
        (
            f"{db_user.full_name or db_user.email} quiere {plan.name} "
            f"({payload.billing_cycle}) vía {payload.method.value}. "
            f"Correlation ID: {correlation_id}"
        ),
    )

    settings = get_settings()
    try:
        await send_purchase_request(
            admin_email=settings.admin_email,
            user_email=db_user.email,
            user_name=db_user.full_name or db_user.email,
            plan_key=plan.key,
            billing_cycle=payload.billing_cycle,
            method=payload.method.value,
            phone=payload.phone,
            note=payload.note,
            correlation_id=correlation_id,
        )
    except Exception:
        logger.exception("Failed to send purchase notification email")
    await db.flush()

    return PurchaseRequestOut(
        ok=True,
        correlation_id=correlation_id,
        message=t("billing.purchaseReceived", locale),
        whatsapp_number=await get_whatsapp_number(),
    )
