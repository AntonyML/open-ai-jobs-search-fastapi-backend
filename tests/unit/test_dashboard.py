"""Tests for the dashboard/analytics endpoints (stats, funnel, trends)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.dashboard import get_analytics_trends
from app.db.models import (
    Application,
    Base,
    InterviewPrep,
    JobPosting,
    Outcome,
    RankEvaluation,
    User,
)

USER_ID = "user-1"

# The endpoint is decorated with aiocache's in-memory cache; unit tests call
# the undecorated function so a cached result never leaks across tests.
trends_handler = get_analytics_trends.__wrapped__


@pytest.fixture
async def db_session():
    """In-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            id=USER_ID,
            email="test@example.com",
            hashed_password="fakehash",
            full_name="Test User",
        )
        session.add(user)
        await session.commit()
        yield session


async def _seed_activity(db: AsyncSession, **counts: int) -> None:
    """Create the FK chain job → rank eval → application → interview/outcome."""
    now = datetime.now(timezone.utc)
    job = JobPosting(
        user_id=USER_ID,
        portal="linkedin",
        external_id="main-job",
        title="Senior Engineer",
        created_at=now,
    )
    db.add(job)
    await db.flush()

    rank = RankEvaluation(
        job_posting_id=job.id,
        user_id=USER_ID,
        verdict="Strong Fit",
        location_status="PASS",
        created_at=now,
    )
    db.add(rank)
    await db.flush()

    app = Application(
        user_id=USER_ID,
        job_posting_id=job.id,
        rank_evaluation_id=rank.id,
        created_at=now,
    )
    db.add(app)
    await db.flush()

    if counts.get("interviews"):
        db.add(InterviewPrep(
            user_id=USER_ID,
            application_id=app.id,
            stage="technical",
            created_at=now,
        ))

    if counts.get("hired"):
        db.add(Outcome(
            user_id=USER_ID,
            application_id=app.id,
            status="hired",
            created_at=now,
        ))

    await db.commit()


@pytest.mark.asyncio
async def test_trends_zero_filled_for_empty_user(db_session):
    """A user with no activity gets zero-filled buckets for every day."""
    res = await trends_handler(user={"sub": USER_ID}, db=db_session, days=7)

    assert res["days"] == 7
    assert len(res["trends"]) == 7
    for row in res["trends"]:
        assert row["scraped"] == 0
        assert row["applications"] == 0
        assert row["interviews"] == 0
        assert row["ranked"] == 0
        assert row["hired"] == 0


@pytest.mark.asyncio
async def test_trends_counts_activity_in_today_bucket(db_session):
    """Seeded activity is counted in the last (today) bucket."""
    await _seed_activity(db_session, scraped=1, applications=1, interviews=1, ranked=1, hired=1)

    res = await trends_handler(user={"sub": USER_ID}, db=db_session, days=14)

    assert len(res["trends"]) == 14
    today = res["trends"][-1]
    assert today["scraped"] == 1
    assert today["applications"] == 1
    assert today["interviews"] == 1
    assert today["ranked"] == 1
    assert today["hired"] == 1

    # Earlier buckets remain empty
    assert res["trends"][0]["applications"] == 0


@pytest.mark.asyncio
async def test_trends_respects_days_param(db_session):
    """The window length follows the `days` parameter."""
    await _seed_activity(db_session, applications=1)

    res = await trends_handler(user={"sub": USER_ID}, db=db_session, days=7)
    assert len(res["trends"]) == 7
    assert res["trends"][-1]["applications"] == 1
