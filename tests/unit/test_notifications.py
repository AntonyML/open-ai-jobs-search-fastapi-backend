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
