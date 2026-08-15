"""Notifications service — cross-cutting helpers for app_notifications."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppConfig, AppNotification, User

# TTL: notifications older than this are purged lazily on read.
NOTIFICATION_TTL_DAYS = 30

# AppConfig key where the admin-tunable TTL (days) lives.  Stored as
# {"days": N} to match the ``credit_costs`` AppConfig pattern.
NOTIFICATION_TTL_CONFIG_KEY = "notification_ttl_days"


async def notify_admin(
    db: AsyncSession,
    type_: str,
    title: str,
    body: str | None = None,
    payload: dict | None = None,
) -> bool:
    """Create an in-app notification for the first admin user.

    Returns ``True`` when an admin exists and the notification was created,
    ``False`` otherwise (no admin configured).
    """
    result = await db.execute(
        select(User).where(User.role == "admin").order_by(User.created_at.asc()).limit(1)
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        return False
    db.add(
        AppNotification(
            user_id=admin.id,
            type=type_,
            title=title,
            body=body,
            payload=payload,
        )
    )
    await db.flush()
    return True


async def get_notification_ttl_days(db: AsyncSession) -> int:
    """Return the effective notification retention in days.

    Reads the admin-configurable value from ``app_config`` under
    ``NOTIFICATION_TTL_CONFIG_KEY``; falls back to ``NOTIFICATION_TTL_DAYS``
    when unset or invalid.
    """
    result = await db.execute(
        select(AppConfig).where(AppConfig.key == NOTIFICATION_TTL_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    stored = (row.value if row is not None else None) or {}
    days = stored.get("days")
    if isinstance(days, int) and not isinstance(days, bool) and days > 0:
        return days
    return NOTIFICATION_TTL_DAYS


async def set_notification_ttl_days(db: AsyncSession, days: int) -> int:
    """Persist the admin-tunable notification retention (days).

    Clamps to a minimum of 1 day and upserts the ``app_config`` row.
    Returns the effective value.
    """
    days = max(1, int(days))
    result = await db.execute(
        select(AppConfig).where(AppConfig.key == NOTIFICATION_TTL_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = AppConfig(key=NOTIFICATION_TTL_CONFIG_KEY, value={"days": days})
        db.add(row)
    else:
        row.value = {**(row.value or {}), "days": days}
    await db.flush()
    return days


async def purge_expired_notifications(
    db: AsyncSession,
    max_age_days: int | None = None,
) -> int:
    """Delete notifications older than the TTL.

    When ``max_age_days`` is None (the default), the admin-configurable
    value from ``app_config`` is used.  Called lazily from the notifications
    router and once at startup so
    no background scheduler is needed — the table self-cleans on every read.
    Returns the number of rows removed.
    """
    if max_age_days is None:
        max_age_days = await get_notification_ttl_days(db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    result = await db.execute(
        delete(AppNotification).where(AppNotification.created_at < cutoff)
    )
    await db.flush()
    return max(result.rowcount or 0, 0)


async def mark_notifications_read(
    db: AsyncSession,
    admin_id: str,
    user_id: str,
    type_: str,
    correlation_id: str | None = None,
) -> int:
    """Mark the admin's unread notifications of ``type_`` for ``user_id`` as read.

    Called after the admin resolves a pending request (purchase, top-up,
    refund) so the bell no longer shows the pending-request badge.  When
    ``correlation_id`` is given, only the notification(s) carrying that
    correlation id in the payload are matched.  Returns the number marked.
    """
    result = await db.execute(
        select(AppNotification.id).where(
            AppNotification.user_id == admin_id,
            AppNotification.type == type_,
            AppNotification.is_read == False,  # noqa: E712
        )
    )
    candidate_ids = [row[0] for row in result.all()]
    if not candidate_ids:
        return 0

    # Filter by payload in Python — JSONB path queries differ between
    # SQLite (tests) and PostgreSQL, and candidate sets are tiny.
    payloads = await db.execute(
        select(AppNotification.id, AppNotification.payload).where(
            AppNotification.id.in_(candidate_ids)
        )
    )
    matched = [
        nid
        for nid, payload in payloads.all()
        if (payload or {}).get("user_id") == user_id
        and (
            correlation_id is None
            or (payload or {}).get("correlation_id") == correlation_id
        )
    ]
    if not matched:
        return 0

    await db.execute(
        update(AppNotification)
        .where(AppNotification.id.in_(matched))
        .values(is_read=True)
    )
    await db.flush()
    return len(matched)


async def mark_purchase_requests_read(
    db: AsyncSession,
    admin_id: str,
    user_id: str,
) -> int:
    """Mark the admin's unread ``purchase_request`` notifications as read.

    Backward-compatible wrapper around :func:`mark_notifications_read`.
    """
    return await mark_notifications_read(db, admin_id, user_id, "purchase_request")


async def mark_topup_requests_read(
    db: AsyncSession,
    admin_id: str,
    user_id: str,
    correlation_id: str | None = None,
) -> int:
    """Mark the admin's unread ``topup_request`` notifications as read.

    Called after the admin approves a top-up for ``user_id``.
    """
    return await mark_notifications_read(
        db, admin_id, user_id, "topup_request", correlation_id=correlation_id
    )


async def mark_refund_requests_read(
    db: AsyncSession,
    admin_id: str,
    user_id: str,
    correlation_id: str | None = None,
) -> int:
    """Mark the admin's unread ``refund_request`` notifications as read.

    Called after the admin approves a refund for ``user_id``.
    """
    return await mark_notifications_read(
        db, admin_id, user_id, "refund_request", correlation_id=correlation_id
    )


async def mark_upgrade_requests_read(
    db: AsyncSession,
    admin_id: str,
    user_id: str,
    correlation_id: str | None = None,
) -> int:
    """Mark the admin's unread ``upgrade_prorate`` notifications as read.

    Called after the admin activates the new plan for ``user_id``.
    """
    return await mark_notifications_read(
        db, admin_id, user_id, "upgrade_prorate", correlation_id=correlation_id
    )
