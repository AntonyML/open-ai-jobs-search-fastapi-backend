"""Tests for the admin user listing endpoint (pagination, filters, sorting, stats)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.admin import list_subscriptions, list_users
from app.db.models import Base, User, UserSubscription


@pytest.fixture
async def db_session():
    """In-memory SQLite database with a set of test users."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        users = [
            User(
                id="u-admin",
                email="admin@example.com",
                hashed_password="fakehash",
                full_name="Admin User",
                role="admin",
                tier="premium",
            ),
            User(
                id="u-alice",
                email="alice@example.com",
                hashed_password="fakehash",
                full_name="Alice Example",
                role="client",
                tier="premium",
            ),
            User(
                id="u-bob",
                email="bob@example.com",
                hashed_password="fakehash",
                full_name="Bob Example",
                role="client",
                tier="free",
            ),
            User(
                id="u-carol",
                email="carol@example.com",
                hashed_password="fakehash",
                full_name="Carol Example",
                role="client",
                tier="free",
            ),
        ]
        session.add_all(users)
        await session.commit()
        yield session
    await engine.dispose()


class TestListUsersPagination:
    @pytest.mark.asyncio
    async def test_default_page_returns_paged_result(self, db_session):
        result = await list_users(
            admin={"role": "admin"}, db=db_session
        )
        assert result.total == 4
        assert result.page == 1
        assert result.page_size == 5
        assert len(result.items) == 4
        # plan.md §2.7: the legacy ``premium`` stat is replaced by ``active_subs``.
        assert result.stats == {"total": 4, "admins": 1, "active_subs": 0}

    @pytest.mark.asyncio
    async def test_page_size_limits_rows(self, db_session):
        result = await list_users(admin={"role": "admin"}, db=db_session, page_size=2)
        assert len(result.items) == 2
        assert result.total == 4

    @pytest.mark.asyncio
    async def test_second_page_returns_remaining(self, db_session):
        result = await list_users(
            admin={"role": "admin"}, db=db_session, page_size=3, page=2
        )
        assert len(result.items) == 1
        assert result.total == 4

    @pytest.mark.asyncio
    async def test_page_size_clamped_to_max(self, db_session):
        result = await list_users(
            admin={"role": "admin"}, db=db_session, page_size=500
        )
        assert result.page_size == 100

    @pytest.mark.asyncio
    async def test_page_at_least_one(self, db_session):
        result = await list_users(admin={"role": "admin"}, db=db_session, page=0)
        assert result.page == 1


class TestListUsersFilters:
    @pytest.mark.asyncio
    async def test_filter_by_role(self, db_session):
        result = await list_users(admin={"role": "admin"}, db=db_session, role="admin")
        assert result.total == 1
        assert result.items[0].id == "u-admin"

    @pytest.mark.asyncio
    async def test_filter_by_tier(self, db_session):
        result = await list_users(admin={"role": "admin"}, db=db_session, tier="premium")
        assert result.total == 2

    @pytest.mark.asyncio
    async def test_search_by_email(self, db_session):
        result = await list_users(admin={"role": "admin"}, db=db_session, search="alice")
        assert result.total == 1
        assert result.items[0].id == "u-alice"

    @pytest.mark.asyncio
    async def test_search_by_full_name(self, db_session):
        result = await list_users(admin={"role": "admin"}, db=db_session, search="bob")
        assert result.total == 1
        assert result.items[0].id == "u-bob"


class TestListUsersSorting:
    @pytest.mark.asyncio
    async def test_sort_ascending(self, db_session):
        result = await list_users(
            admin={"role": "admin"}, db=db_session, sort="email", order="asc"
        )
        emails = [u.email for u in result.items]
        assert emails == sorted(emails)

    @pytest.mark.asyncio
    async def test_sort_descending_default(self, db_session):
        result = await list_users(
            admin={"role": "admin"}, db=db_session, sort="email"
        )
        emails = [u.email for u in result.items]
        assert emails == sorted(emails, reverse=True)

    @pytest.mark.asyncio
    async def test_invalid_sort_falls_back_to_created_at(self, db_session):
        result = await list_users(
            admin={"role": "admin"}, db=db_session, sort="not_a_column"
        )
        assert result.total == 4


class TestListUsersStats:
    @pytest.mark.asyncio
    async def test_active_subs_counts_users_with_active_subscription(self, db_session):
        """active_subs = distinct users with status == 'active' (not rows)."""
        db_session.add_all([
            UserSubscription(user_id="u-alice", plan_key="pro", status="active", source="admin"),
            UserSubscription(user_id="u-bob", plan_key="max", status="active", source="admin"),
            # A cancelled sub must NOT count, nor a second active row for the
            # same user (distinct user_id).
            UserSubscription(user_id="u-carol", plan_key="pro", status="cancelled", source="admin"),
            UserSubscription(user_id="u-bob", plan_key="pro", status="active", source="admin"),
        ])
        await db_session.commit()

        result = await list_users(admin={"role": "admin"}, db=db_session)
        assert result.stats == {"total": 4, "admins": 1, "active_subs": 2}


class TestListSubscriptions:
    @pytest.mark.asyncio
    async def test_filter_by_user_id(self, db_session):
        db_session.add_all([
            UserSubscription(user_id="u-alice", plan_key="pro", status="active", source="admin"),
            UserSubscription(user_id="u-bob", plan_key="max", status="active", source="admin"),
        ])
        await db_session.commit()

        result = await list_subscriptions(
            admin={"role": "admin"}, db=db_session, user_id="u-alice"
        )
        assert len(result) == 1
        assert result[0].user_id == "u-alice"
        assert result[0].plan_key == "pro"

    @pytest.mark.asyncio
    async def test_user_id_filter_combines_with_status(self, db_session):
        db_session.add_all([
            UserSubscription(user_id="u-alice", plan_key="pro", status="active", source="admin"),
            UserSubscription(user_id="u-alice", plan_key="max", status="cancelled", source="admin"),
        ])
        await db_session.commit()

        result = await list_subscriptions(
            admin={"role": "admin"},
            db=db_session,
            user_id="u-alice",
            status_filter="active",
        )
        assert len(result) == 1
        assert result[0].plan_key == "pro"

    @pytest.mark.asyncio
    async def test_no_user_id_returns_all(self, db_session):
        db_session.add_all([
            UserSubscription(user_id="u-alice", plan_key="pro", status="active", source="admin"),
            UserSubscription(user_id="u-bob", plan_key="max", status="active", source="admin"),
        ])
        await db_session.commit()

        result = await list_subscriptions(admin={"role": "admin"}, db=db_session)
        assert len(result) == 2
