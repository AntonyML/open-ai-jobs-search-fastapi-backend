"""Notifications service — cross-cutting helpers for app_notifications."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppNotification

# TTL: notifications older than this are purged lazily on read.
NOTIFICATION_TTL_DAYS = 30


async def purge_expired_notifications(
    db: AsyncSession,
    max_age_days: int = NOTIFICATION_TTL_DAYS,
) -> int:
    """Delete notifications older than ``max_age_days`` (TTL).

    Called lazily from the notifications router (same pattern as
    ``seed_default_plans``) so no background scheduler is needed — the
    table self-cleans on every read.  Returns the number of rows removed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    result = await db.execute(
        delete(AppNotification).where(AppNotification.created_at < cutoff)
    )
    await db.flush()
    return max(result.rowcount or 0, 0)


async def mark_purchase_requests_read(
    db: AsyncSession,
    admin_id: str,
    user_id: str,
) -> int:
    """Mark the admin's unread ``purchase_request`` notifications as read.

    Called after the admin activates a subscription for ``user_id`` so the
    bell no longer shows the pending-request badge for that purchase.
    Returns the number of notifications marked.
    """
    result = await db.execute(
        select(AppNotification.id).where(
            AppNotification.user_id == admin_id,
            AppNotification.type == "purchase_request",
            AppNotification.is_read == False,  # noqa: E712
        )
    )
    candidate_ids = [row[0] for row in result.all()]
    if not candidate_ids:
        return 0

    # Filter by payload.user_id in Python — JSONB path queries differ between
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
