"""Notifications service — cross-cutting helpers for app_notifications."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppNotification


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
