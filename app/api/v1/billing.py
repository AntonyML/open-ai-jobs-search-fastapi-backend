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
from app.db.models import User
from app.schemas.billing import (
    CancelSubscriptionOut,
    CreditStatusOut,
    CreditTransactionOut,
    ProductCatalogOut,
    PurchaseRequest,
    PurchaseRequestOut,
    RefundRequestOut,
    TopupRequest,
    TopupRequestOut,
    UpgradeRequest,
    UpgradeRequestOut,
    UserSubscriptionOut,
)
from app.services import credits
from app.services.billing_policy import (
    billing_cycle_for,
    check_refund_eligibility,
    compute_prorated_due,
    compute_usage_in_period,
    get_billing_policy,
)
from app.services.email import (
    send_prorated_upgrade_request,
    send_purchase_request,
    send_refund_request,
    send_topup_request,
)
from app.services.notifications import notify_admin
from app.services.plans import build_catalog, get_plan, get_whatsapp_number
from app.services.subscriptions import (
    cancel_subscription,
    ensure_admin_subscription,
    get_active_subscription,
    get_user_access,
    process_expired_subscriptions,
)
from app.services.topups import get_paid_subscription, get_topup_packs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


async def _notify_admin(
    db: AsyncSession,
    type_: str,
    title: str,
    body: str | None,
    payload: dict | None = None,
) -> None:
    """Create an in-app notification for the first admin user."""
    await notify_admin(db, type_, title, body, payload)


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
        next_reset_at=balance["next_quota_reset_at"],
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
    return ProductCatalogOut(**await build_catalog(db))


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
        payload={
            "user_id": db_user.id,
            "user_email": db_user.email,
            "user_name": db_user.full_name,
            "plan_key": plan.key,
            "billing_cycle": payload.billing_cycle,
            "correlation_id": correlation_id,
        },
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


@router.post("/topup", response_model=TopupRequestOut)
async def request_topup(
    payload: TopupRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
) -> TopupRequestOut:
    """Request a manual credit top-up (paid plans only).

    Validates the pack against the fixed ``topup_packs`` catalog and the
    paid-plan rule, then records a ``topup_request`` notification for the
    admin (money moves manually via SINPE/WhatsApp).  The admin applies the
    credits from the admin panel — this endpoint never touches the balance.
    """
    packs = await get_topup_packs(db)
    pack = next((p for p in packs if int(p["credits"]) == payload.pack_credits), None)
    if pack is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_topup_pack",
                "message": "Unknown top-up pack",
            },
        )

    # Free users cannot top up: their credits reset to the weekly allowance,
    # so a top-up would be wiped at the next refill (plan.md §9.1).
    subscription, _plan = await get_paid_subscription(db, user["sub"])
    if subscription is None:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail={
                "code": "topup_requires_plan",
                "message": t("billing.topupRequiresPlan", locale),
            },
        )

    correlation_id = uuid.uuid4().hex
    result = await db.execute(select(User).where(User.id == user["sub"]))
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="User not found")

    pack_clean = {"price_usd": float(pack["price_usd"]), "credits": int(pack["credits"])}
    await _notify_admin(
        db,
        "topup_request",
        f"Solicitud de top-up: {pack_clean['credits']} créditos",
        (
            f"{db_user.full_name or db_user.email} quiere {pack_clean['credits']} créditos "
            f"(${pack_clean['price_usd']:.2f}) vía {payload.method.value}. "
            f"Correlation ID: {correlation_id}"
        ),
        payload={
            "user_id": db_user.id,
            "user_email": db_user.email,
            "user_name": db_user.full_name,
            "pack": pack_clean,
            "price_usd": pack_clean["price_usd"],
            "credits": pack_clean["credits"],
            "method": payload.method.value,
            "correlation_id": correlation_id,
        },
    )

    settings = get_settings()
    try:
        await send_topup_request(
            admin_email=settings.admin_email,
            user_email=db_user.email,
            user_name=db_user.full_name or db_user.email,
            pack_credits=pack_clean["credits"],
            price_usd=pack_clean["price_usd"],
            method=payload.method.value,
            phone=payload.phone,
            note=payload.note,
            correlation_id=correlation_id,
        )
    except Exception:
        logger.exception("Failed to send top-up notification email")
    await db.flush()

    return TopupRequestOut(
        ok=True,
        correlation_id=correlation_id,
        message=t("billing.topupReceived", locale),
        whatsapp_number=await get_whatsapp_number(),
        pack=pack_clean,
    )


@router.post("/cancel", response_model=CancelSubscriptionOut)
async def cancel_my_subscription(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
) -> CancelSubscriptionOut:
    """Cancel the user's active subscription (paid days remain intact).

    Stops auto-renewal and stamps ``cancelled_at``; the subscription stays
    ``active`` until ``period_end`` (plan.md §2 Caso 4), then
    ``process_expired_subscriptions`` expires it and drops the tier to free.
    """
    sub = await get_active_subscription(db, user["sub"])
    if sub is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=t("billing.noActiveSubscription", locale),
        )

    await cancel_subscription(db, sub)
    await db.flush()
    return CancelSubscriptionOut(
        ok=True,
        message=(
            t("billing.cancelled", locale)
            if sub.period_end is None
            else t("billing.cancelledUntil", locale, date=sub.period_end.strftime("%Y-%m-%d"))
        ),
        period_end=sub.period_end,
    )


@router.post("/refund", response_model=RefundRequestOut)
async def request_refund(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
) -> RefundRequestOut:
    """Request a refund for the active subscription (policy-gated).

    The policy blocks the request **at origin** (plan.md §2/§3): monthly
    refunds are refused when the user has consumed >= ``refund_credit_threshold``
    credits in the current period; annual refunds are a hard 14-day window.
    The admin therefore never sees an invalid ``refund_request``.

    The request only records intent (``refund_request`` notification + email);
    the money moves manually and the admin executes the zero-out from the
    admin panel.
    """
    sub = await get_active_subscription(db, user["sub"])
    if sub is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=t("billing.noActiveSubscription", locale),
        )

    usage = await compute_usage_in_period(db, sub)
    policy = await get_billing_policy(db)
    eligible, reason = check_refund_eligibility(sub, usage, policy)
    if not eligible:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail={
                "code": reason,
                "message": t(
                    {
                        "refund_usage_exceeded": "billing.refundUsageBlocked",
                        "refund_cooling_passed": "billing.refundCoolingBlocked",
                    }.get(reason, "billing.refundUsageBlocked"),
                    locale,
                ),
            },
        )

    correlation_id = uuid.uuid4().hex
    result = await db.execute(select(User).where(User.id == user["sub"]))
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="User not found")

    await _notify_admin(
        db,
        "refund_request",
        f"Solicitud de reembolso: {sub.plan_key}",
        (
            f"{db_user.full_name or db_user.email} pide reembolso de {sub.plan_key} "
            f"(uso del periodo: {usage} créditos). Correlation ID: {correlation_id}"
        ),
        payload={
            "user_id": db_user.id,
            "user_email": db_user.email,
            "user_name": db_user.full_name,
            "plan_key": sub.plan_key,
            "period_start": sub.period_start.isoformat() if sub.period_start else None,
            "period_end": sub.period_end.isoformat() if sub.period_end else None,
            "usage_in_period": usage,
            "correlation_id": correlation_id,
        },
    )

    settings = get_settings()
    try:
        await send_refund_request(
            admin_email=settings.admin_email,
            user_email=db_user.email,
            user_name=db_user.full_name or db_user.email,
            plan_key=sub.plan_key,
            usage_in_period=usage,
            correlation_id=correlation_id,
        )
    except Exception:
        logger.exception("Failed to send refund notification email")
    await db.flush()

    return RefundRequestOut(
        ok=True,
        correlation_id=correlation_id,
        message=t("billing.refundReceived", locale),
        whatsapp_number=await get_whatsapp_number(),
    )


@router.post("/upgrade", response_model=UpgradeRequestOut)
async def request_upgrade(
    payload: UpgradeRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(get_locale),
) -> UpgradeRequestOut:
    """Request a prorated upgrade to a higher plan (same billing cycle).

    ``amount_due`` = unused portion of the target plan minus the unused
    portion of the current plan, over the remaining period (plan.md §2
    Caso 5).  The cycle is preserved (monthly→monthly, yearly→yearly —
    plan.md §9.2); downgrades and same-plan requests are rejected with 422
    (the user would cancel + re-subscribe instead).  Only the request is
    recorded (``upgrade_prorate`` notification + email); the admin activates
    the new plan with ``price_paid`` = ``amount_due``.
    """
    sub = await get_active_subscription(db, user["sub"])
    if sub is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=t("billing.noActiveSubscription", locale),
        )

    plan_from = await get_plan(db, sub.plan_key)
    plan_to = await get_plan(db, payload.plan_key)
    if plan_to is None or not plan_to.is_active:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Unknown or inactive plan",
        )
    if plan_from is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Current plan not found",
        )

    amount_due = compute_prorated_due(
        plan_from, plan_to, sub.period_start, sub.period_end
    )
    if amount_due <= 0:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "not_an_upgrade",
                "message": t("billing.notAnUpgrade", locale),
            },
        )

    correlation_id = uuid.uuid4().hex
    result = await db.execute(select(User).where(User.id == user["sub"]))
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="User not found")

    await _notify_admin(
        db,
        "upgrade_prorate",
        f"Solicitud de upgrade prorrateado: {plan_to.name}",
        (
            f"{db_user.full_name or db_user.email} quiere pasar de {plan_from.key} "
            f"a {plan_to.key}. Monto prorrateado: ${amount_due:.2f} "
            f"vía {payload.method.value}. Correlation ID: {correlation_id}"
        ),
        payload={
            "user_id": db_user.id,
            "user_email": db_user.email,
            "user_name": db_user.full_name,
            "plan_from": plan_from.key,
            "plan_to": plan_to.key,
            "amount_due": amount_due,
            # Upgrades keep the user's current billing cycle (plan.md §9.2) —
            # the admin activates with the same cycle so the prorate holds.
            "billing_cycle": billing_cycle_for(sub),
            "method": payload.method.value,
            "correlation_id": correlation_id,
        },
    )

    settings = get_settings()
    try:
        await send_prorated_upgrade_request(
            admin_email=settings.admin_email,
            user_email=db_user.email,
            user_name=db_user.full_name or db_user.email,
            plan_from=plan_from.key,
            plan_to=plan_to.key,
            amount_due=amount_due,
            method=payload.method.value,
            phone=payload.phone,
            note=payload.note,
            correlation_id=correlation_id,
        )
    except Exception:
        logger.exception("Failed to send prorated upgrade email")
    await db.flush()

    return UpgradeRequestOut(
        ok=True,
        correlation_id=correlation_id,
        message=t("billing.upgradeReceived", locale),
        amount_due=amount_due,
        whatsapp_number=await get_whatsapp_number(),
    )
