"""Notifications router — server-side notifications (app_notifications).

The frontend NotificationBell reads these instead of localStorage-only
history.  Admin purchase requests (SINPE / WhatsApp) land here via
``_notify_admin`` in the billing router, so the admin sees a badge with
pending requests in the navbar.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models import AppNotification
from app.schemas.notifications import AppNotificationCreate, AppNotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[AppNotificationOut])
async def list_notifications(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    unread_only: bool = False,
) -> list[AppNotificationOut]:
    """Return the authenticated user's notifications, newest first."""
    stmt = (
        select(AppNotification)
        .where(AppNotification.user_id == user["sub"])
        .order_by(AppNotification.created_at.desc())
        .limit(50)
    )
    if unread_only:
        stmt = stmt.where(AppNotification.is_read == False)  # noqa: E712
    result = await db.execute(stmt)
    return [AppNotificationOut.model_validate(x) for x in result.scalars().all()]


@router.post("", response_model=AppNotificationOut, status_code=201)
async def create_notification(
    payload: AppNotificationCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AppNotificationOut:
    """Create a notification for the authenticated user (pipeline events).

    Replaces the localStorage-only ``addNotification`` frontend helper.
    """
    notif = AppNotification(
        user_id=user["sub"],
        type=payload.type,
        title=payload.title,
        body=payload.body,
    )
    db.add(notif)
    await db.flush()
    await db.refresh(notif)
    return AppNotificationOut.model_validate(notif)


@router.delete("")
async def clear_notifications(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete every notification of the user (replaces ``clearNotifications``)."""
    await db.execute(
        delete(AppNotification).where(AppNotification.user_id == user["sub"])
    )
    await db.flush()
    return {"ok": True}


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark a notification as read (only the owner can)."""
    await db.execute(
        update(AppNotification)
        .where(
            AppNotification.id == notification_id,
            AppNotification.user_id == user["sub"],
        )
        .values(is_read=True)
    )
    await db.flush()
    return {"ok": True}


@router.post("/read-all")
async def mark_all_notifications_read(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark every notification of the user as read."""
    await db.execute(
        update(AppNotification)
        .where(AppNotification.user_id == user["sub"])
        .values(is_read=True)
    )
    await db.flush()
    return {"ok": True}
