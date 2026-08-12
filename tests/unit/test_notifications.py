"""Tests for the notifications router (app_notifications)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db
from app.db.models import AppNotification, Base, User


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def _make_user(db: AsyncSession, user_id: str, email: str, role: str = "client") -> User:
    u = User(id=user_id, email=email, hashed_password="x", role=role, tier="free")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def test_list_returns_only_own_notifications(db_session):
    admin = await _make_user(db_session, "admin-1", "admin@example.com", role="admin")
    client = await _make_user(db_session, "user-1", "u1@example.com")

    db_session.add_all(
        [
            AppNotification(
                user_id=admin.id,
                type="purchase_request",
                title="Solicitud de compra: Pro",
                body="user quiere Pro (monthly) vía sinpe. Correlation ID: abc123",
            ),
            AppNotification(
                user_id=client.id,
                type="credits_low",
                title="Créditos bajos",
                body="Te quedan 2 créditos",
            ),
            AppNotification(
                user_id=admin.id,
                type="quota_exhausted",
                title="Cuota agotada",
                body="Cuota diaria alcanzada",
                is_read=True,
            ),
        ]
    )
    await db_session.commit()

    # Simulate the router query for the admin.
    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(AppNotification)
            .where(AppNotification.user_id == admin.id)
            .order_by(AppNotification.created_at.desc())
        )
    ).scalars().all()

    assert len(rows) == 2
    assert {r.type for r in rows} == {"purchase_request", "quota_exhausted"}
    # Newest first ordering: quota_exhausted (created later) before purchase_request.
    assert rows[0].type == "quota_exhausted"


async def test_mark_read_only_owner(db_session):
    admin = await _make_user(db_session, "admin-1", "admin@example.com", role="admin")
    client = await _make_user(db_session, "user-1", "u1@example.com")

    n = AppNotification(user_id=admin.id, type="purchase_request", title="Compra", body="x")
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)
    assert n.is_read is False

    # Owner can mark as read.
    from sqlalchemy import update

    await db_session.execute(
        update(AppNotification)
        .where(AppNotification.id == n.id, AppNotification.user_id == admin.id)
        .values(is_read=True)
    )
    await db_session.flush()
    await db_session.refresh(n)
    assert n.is_read is True

    # Non-owner cannot (update matches nothing — row count 0).
    result = await db_session.execute(
        update(AppNotification)
        .where(AppNotification.id == n.id, AppNotification.user_id == client.id)
        .values(is_read=False)
    )
    assert result.rowcount == 0


# ── Endpoint-level tests (TestClient + real tokens) ─────────────────


@pytest.fixture
def api_client(db_session):
    """TestClient with the real router but an in-memory DB dependency."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _token(user_id: str, role: str = "client") -> str:
    from app.core.security import create_access_token

    return create_access_token(subject=user_id, role=role)


def test_list_mark_read_and_read_all_endpoints(api_client, db_session):
    import asyncio

    admin_id = "admin-ep-1"

    async def setup():
        await _make_user(db_session, admin_id, "admin-ep@example.com", role="admin")
        db_session.add(
            AppNotification(
                user_id=admin_id,
                type="purchase_request",
                title="Solicitud: Pro",
                body="user quiere Pro. Correlation ID: abc",
            )
        )
        db_session.add(
            AppNotification(
                user_id=admin_id,
                type="quota_exhausted",
                title="Cuota agotada",
                body="x",
            )
        )
        await db_session.commit()

    asyncio.run(setup())

    headers = {"Authorization": f"Bearer {_token(admin_id, role='admin')}"}

    # 1. List returns both notifications.
    res = api_client.get("/api/v1/notifications", headers=headers)
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 2
    assert {r["type"] for r in rows} == {"purchase_request", "quota_exhausted"}

    # 2. unread_only filter.
    res = api_client.get("/api/v1/notifications?unread_only=true", headers=headers)
    assert res.status_code == 200
    assert len(res.json()) == 2

    # 3. Mark one read.
    nid = rows[0]["id"]
    res = api_client.post(f"/api/v1/notifications/{nid}/read", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    res = api_client.get("/api/v1/notifications?unread_only=true", headers=headers)
    assert len(res.json()) == 1

    # 4. Read-all.
    res = api_client.post("/api/v1/notifications/read-all", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    res = api_client.get("/api/v1/notifications?unread_only=true", headers=headers)
    assert len(res.json()) == 0

    # 5. Unauthenticated → 401.
    res = api_client.get("/api/v1/notifications")
    assert res.status_code == 401

    # 6. A different user cannot see the admin's notifications.
    other = "user-ep-1"
    asyncio.run(_make_user(db_session, other, "other@example.com"))
    res = api_client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {_token(other)}"},
    )
    assert res.status_code == 200
    assert res.json() == []


def test_create_and_clear_endpoints(api_client, db_session):
    import asyncio

    user_id = "user-ep-2"
    asyncio.run(_make_user(db_session, user_id, "ep2@example.com"))
    headers = {"Authorization": f"Bearer {_token(user_id)}"}

    # Create a pipeline notification.
    res = api_client.post(
        "/api/v1/notifications",
        headers=headers,
        json={"type": "rank", "title": "Ranking done", "body": "Evaluated 5 jobs"},
    )
    assert res.status_code == 201
    created = res.json()
    assert created["type"] == "rank"
    assert created["title"] == "Ranking done"
    assert created["is_read"] is False

    # Payload round-trips (deep-link metadata for purchase requests).
    res = api_client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "type": "purchase_request",
            "title": "Compra: Pro",
            "payload": {"user_id": "x", "plan_key": "pro"},
        },
    )
    assert res.status_code == 201
    assert res.json()["payload"] == {"user_id": "x", "plan_key": "pro"}

    # Create an error notification.
    res = api_client.post(
        "/api/v1/notifications",
        headers=headers,
        json={"type": "rank_error", "title": "Ranking failed", "body": "Rate limit"},
    )
    assert res.status_code == 201

    # All three appear in the list (rank + rank_error + purchase_request).
    res = api_client.get("/api/v1/notifications", headers=headers)
    assert len(res.json()) == 3

    # Validation: missing title → 422.
    res = api_client.post(
        "/api/v1/notifications", headers=headers, json={"type": "rank"}
    )
    assert res.status_code == 422

    # Clear removes everything.
    res = api_client.delete("/api/v1/notifications", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    res = api_client.get("/api/v1/notifications", headers=headers)
    assert res.json() == []


# ── Auto-mark purchase requests on subscription activation ──────────


async def test_mark_purchase_requests_read_matches_payload_user(db_session):
    from app.services.notifications import mark_purchase_requests_read

    admin = await _make_user(db_session, "admin-1", "admin@example.com", role="admin")

    db_session.add_all(
        [
            AppNotification(
                user_id=admin.id,
                type="purchase_request",
                title="Compra: Pro (user-A)",
                payload={"user_id": "user-A", "plan_key": "pro"},
            ),
            AppNotification(
                user_id=admin.id,
                type="purchase_request",
                title="Compra: Pro (user-B)",
                payload={"user_id": "user-B", "plan_key": "pro"},
            ),
            AppNotification(
                user_id=admin.id,
                type="purchase_request",
                title="Compra ya leída (user-A)",
                payload={"user_id": "user-A", "plan_key": "pro"},
                is_read=True,
            ),
            # Different type — must not be touched.
            AppNotification(
                user_id=admin.id,
                type="rank",
                title="Ranking done",
                payload={"user_id": "user-A"},
            ),
        ]
    )
    await db_session.commit()

    marked = await mark_purchase_requests_read(db_session, admin.id, "user-A")
    await db_session.commit()

    # Only the unread purchase_request for user-A is marked (not user-B,
    # not the already-read one, not the rank notification).
    assert marked == 1

    from sqlalchemy import select

    rows = (
        await db_session.execute(select(AppNotification).order_by(AppNotification.title))
    ).scalars().all()
    state = {r.title: r.is_read for r in rows}
    assert state["Compra: Pro (user-A)"] is True
    assert state["Compra: Pro (user-B)"] is False
    assert state["Compra ya leída (user-A)"] is True
    assert state["Ranking done"] is False


# ── TTL purge (notifications older than 30 days) ─────────────────────


async def test_purge_expired_notifications_deletes_only_old(db_session):
    from datetime import datetime, timedelta, timezone

    from app.services.notifications import purge_expired_notifications

    admin = await _make_user(db_session, "admin-ttl", "ttl@example.com")
    now = datetime.now(timezone.utc)

    db_session.add_all(
        [
            AppNotification(
                user_id=admin.id,
                type="info",
                title="Vieja (>30 días)",
                created_at=now - timedelta(days=31),
            ),
            AppNotification(
                user_id=admin.id,
                type="info",
                title="Reciente",
                created_at=now - timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()

    purged = await purge_expired_notifications(db_session)
    await db_session.commit()

    assert purged == 1
    from sqlalchemy import select

    remaining = (
        (await db_session.execute(select(AppNotification))).scalars().all()
    )
    assert [r.title for r in remaining] == ["Reciente"]


def test_list_endpoint_purges_old_notifications(api_client, db_session):
    import asyncio
    from datetime import datetime, timedelta, timezone

    user_id = "user-ttl-ep"
    asyncio.run(_make_user(db_session, user_id, "ttl-ep@example.com"))

    now = datetime.now(timezone.utc)

    async def seed():
        db_session.add_all(
            [
                AppNotification(
                    user_id=user_id,
                    type="info",
                    title="Vieja",
                    created_at=now - timedelta(days=40),
                ),
                AppNotification(
                    user_id=user_id,
                    type="info",
                    title="Reciente",
                    created_at=now - timedelta(days=2),
                ),
            ]
        )
        await db_session.commit()

    asyncio.run(seed())

    headers = {"Authorization": f"Bearer {_token(user_id)}"}
    res = api_client.get("/api/v1/notifications", headers=headers)
    assert res.status_code == 200
    rows = res.json()
    assert [r["title"] for r in rows] == ["Reciente"]


# ── Admin-configurable TTL (AppConfig) ───────────────────────────────


async def test_get_set_notification_ttl_roundtrip(db_session):
    from sqlalchemy import select

    from app.services.notifications import (
        NOTIFICATION_TTL_DAYS,
        get_notification_ttl_days,
        set_notification_ttl_days,
    )

    # Default when unset.
    assert await get_notification_ttl_days(db_session) == NOTIFICATION_TTL_DAYS

    # Set a custom value and read it back.
    await set_notification_ttl_days(db_session, 7)
    await db_session.commit()
    assert await get_notification_ttl_days(db_session) == 7

    # Clamped to >= 1.
    await set_notification_ttl_days(db_session, 0)
    await db_session.commit()
    assert await get_notification_ttl_days(db_session) == 1

    # Invalid stored value falls back to the default.
    from app.db.models import AppConfig
    from app.services.notifications import NOTIFICATION_TTL_CONFIG_KEY

    row = (
        await db_session.execute(
            select(AppConfig).where(AppConfig.key == NOTIFICATION_TTL_CONFIG_KEY)
        )
    ).scalar_one()
    row.value = {"days": "nope"}
    await db_session.commit()
    assert await get_notification_ttl_days(db_session) == NOTIFICATION_TTL_DAYS


async def test_purge_uses_configured_ttl(db_session):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.services.notifications import purge_expired_notifications, set_notification_ttl_days

    user = await _make_user(db_session, "user-ttl-cfg", "ttl-cfg@example.com")
    now = datetime.now(timezone.utc)

    db_session.add_all(
        [
            AppNotification(
                user_id=user.id,
                type="info",
                title="Vieja (8 días)",
                created_at=now - timedelta(days=8),
            ),
            AppNotification(
                user_id=user.id,
                type="info",
                title="Reciente (2 días)",
                created_at=now - timedelta(days=2),
            ),
        ]
    )
    await db_session.commit()

    # TTL of 7 days → the 8-day-old one is purged, the 2-day-old survives.
    await set_notification_ttl_days(db_session, 7)
    await db_session.commit()
    purged = await purge_expired_notifications(db_session)
    await db_session.commit()

    assert purged == 1
    remaining = (
        (await db_session.execute(select(AppNotification))).scalars().all()
    )
    assert [r.title for r in remaining] == ["Reciente (2 días)"]


# ── Startup TTL purge (runs once on app boot) ────────────────────────


def test_startup_purge_deletes_stale_notifications(api_client, db_session):
    import asyncio
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from app.db import session as db_session_module

    user_id = "user-startup-ttl"
    asyncio.run(_make_user(db_session, user_id, "startup-ttl@example.com"))

    now = datetime.now(timezone.utc)

    async def seed():
        db_session.add_all(
            [
                AppNotification(
                    user_id=user_id,
                    type="info",
                    title="Vieja",
                    created_at=now - timedelta(days=40),
                ),
                AppNotification(
                    user_id=user_id,
                    type="info",
                    title="Reciente",
                    created_at=now - timedelta(days=2),
                ),
            ]
        )
        await db_session.commit()

    asyncio.run(seed())

    app = api_client.app

    # Patch the session factory so the lifespan purge uses the test DB.
    class _FakeFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    with (
        patch.object(db_session_module, "async_session_factory", _FakeFactory()),
        TestClient(app) as client,
    ):
        res = client.get("/health")
        assert res.status_code == 200

    # The 40-day-old notification was purged at boot; the recent one remains.
    from sqlalchemy import select

    async def check():
        remaining = (
            (await db_session.execute(select(AppNotification))).scalars().all()
        )
        return [r.title for r in remaining]

    assert asyncio.run(check()) == ["Reciente"]


def test_admin_notification_ttl_endpoints(api_client, db_session):
    import asyncio

    admin_id = "admin-ttl-ep"
    asyncio.run(_make_user(db_session, admin_id, "ttl-admin@example.com", role="admin"))
    client_id = "client-ttl-ep"
    asyncio.run(_make_user(db_session, client_id, "ttl-client@example.com"))

    admin_headers = {"Authorization": f"Bearer {_token(admin_id, role='admin')}"}
    client_headers = {"Authorization": f"Bearer {_token(client_id)}"}

    # GET returns the default (30).
    res = api_client.get("/api/v1/admin/notification-ttl", headers=admin_headers)
    assert res.status_code == 200
    assert res.json() == {"days": 30}

    # PUT updates the value.
    res = api_client.put(
        "/api/v1/admin/notification-ttl",
        headers=admin_headers,
        json={"days": 14},
    )
    assert res.status_code == 200
    assert res.json() == {"days": 14}

    # Invalid value → 422.
    res = api_client.put(
        "/api/v1/admin/notification-ttl",
        headers=admin_headers,
        json={"days": 0},
    )
    assert res.status_code == 422

    # Non-admin → 403.
    res = api_client.get("/api/v1/admin/notification-ttl", headers=client_headers)
    assert res.status_code == 403

    # The purge now honors the admin-set value through the GET endpoint.
    res = api_client.get("/api/v1/admin/notification-ttl", headers=admin_headers)
    assert res.json() == {"days": 14}


async def test_mark_purchase_requests_read_no_match(db_session):
    from app.services.notifications import mark_purchase_requests_read

    admin = await _make_user(db_session, "admin-1", "admin@example.com", role="admin")
    db_session.add(
        AppNotification(
            user_id=admin.id,
            type="purchase_request",
            title="Compra: Pro (user-X)",
            payload={"user_id": "user-X", "plan_key": "pro"},
        )
    )
    await db_session.commit()

    marked = await mark_purchase_requests_read(db_session, admin.id, "other-user")
    await db_session.commit()
    assert marked == 0

    # The unrelated notification stays unread.
    from sqlalchemy import select

    row = (
        await db_session.execute(select(AppNotification))
    ).scalar_one()
    assert row.is_read is False
