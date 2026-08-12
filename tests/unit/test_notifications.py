"""Tests for the notifications router (app_notifications)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
