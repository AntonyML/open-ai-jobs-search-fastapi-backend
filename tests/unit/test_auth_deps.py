"""Tests for the authorization dependencies in app/api/deps.py.

`require_max_or_admin` gates every pipeline endpoint (search, rank, apply,
interview, expand, upskill, outcome, orchestrator) — both the actions that
spend credits and the read-only GET endpoints. The JWT tier is the first
line of defence; the dependency double-checks against the DB in case the
JWT is stale (e.g. an expired subscription).
"""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import require_max_or_admin
from app.db.models import Base, User


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _add_user(db: AsyncSession, user_id: str, tier: str, role: str = "client") -> None:
    db.add(
        User(
            id=user_id,
            email=f"{user_id}@example.com",
            hashed_password="fakehash",
            full_name=f"User {user_id}",
            tier=tier,
            role=role,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_admin_always_allowed(db_session):
    await _add_user(db_session, "admin-1", tier="free", role="admin")
    user = await require_max_or_admin(user={"sub": "admin-1", "role": "admin", "tier": "free"}, db=db_session)
    assert user["sub"] == "admin-1"


@pytest.mark.asyncio
async def test_max_tier_allowed_from_jwt(db_session):
    await _add_user(db_session, "max-1", tier="max")
    user = await require_max_or_admin(user={"sub": "max-1", "role": "client", "tier": "max"}, db=db_session)
    assert user["sub"] == "max-1"


@pytest.mark.asyncio
async def test_stale_jwt_upgraded_from_db(db_session):
    """A JWT with tier=free is re-checked against the DB — a Max user with a
    stale token is allowed and the user dict is upgraded."""
    await _add_user(db_session, "max-2", tier="max")
    user = await require_max_or_admin(user={"sub": "max-2", "role": "client", "tier": "free"}, db=db_session)
    assert user["tier"] == "max"


@pytest.mark.asyncio
async def test_free_user_blocked(db_session):
    await _add_user(db_session, "free-1", tier="free")
    with pytest.raises(HTTPException) as exc:
        await require_max_or_admin(user={"sub": "free-1", "role": "client", "tier": "free"}, db=db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_expired_subscription_blocked(db_session):
    """JWT says max but the DB says free (subscription expired) → blocked."""
    await _add_user(db_session, "expired-1", tier="free")
    with pytest.raises(HTTPException) as exc:
        await require_max_or_admin(user={"sub": "expired-1", "role": "client", "tier": "max"}, db=db_session)
    assert exc.value.status_code == 403
