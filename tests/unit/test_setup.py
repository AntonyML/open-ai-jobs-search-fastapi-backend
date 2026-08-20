"""Tests for the setup service — candidate profile CRUD.

Uses an in-memory SQLite database for fast, isolated unit tests.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, User
from app.exceptions import NotFoundError, ProfileIncompleteError
from app.services import setup

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        # Create a test user
        user = User(
            id="test-user-id",
            email="test@example.com",
            hashed_password="fakehash",
            full_name="Test User",
        )
        session.add(user)
        await session.commit()
        yield session

    await engine.dispose()


# ── Candidate Profile Tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_profile(db_session):
    """Creating a profile succeeds with valid data."""
    data = {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "location": "Copenhagen, Denmark",
        "phone": "+45 12345678",
        "education": [{"degree": "MSc Computer Science", "institution": "DTU"}],
        "experience": [
            {
                "title": "Software Engineer",
                "company": "Acme Corp",
                "bullets": ["Built X", "Improved Y by 20%"],
            }
        ],
    }
    profile = await setup.create_profile(db_session, "test-user-id", data)

    assert profile.user_id == "test-user-id"
    assert profile.full_name == "Jane Doe"
    assert profile.email == "jane@example.com"
    assert profile.education[0]["degree"] == "MSc Computer Science"
    assert profile.experience[0]["company"] == "Acme Corp"


@pytest.mark.asyncio
async def test_create_profile_duplicate_raises(db_session):
    """Creating a second profile for the same user raises ProfileIncompleteError."""
    data = {"full_name": "Jane Doe"}
    await setup.create_profile(db_session, "test-user-id", data)

    with pytest.raises(ProfileIncompleteError):
        await setup.create_profile(db_session, "test-user-id", {"full_name": "Other"})


@pytest.mark.asyncio
async def test_get_profile_not_found(db_session):
    """Getting a profile that doesn't exist raises NotFoundError."""
    with pytest.raises(NotFoundError):
        await setup.get_profile(db_session, "nonexistent-user")


@pytest.mark.asyncio
async def test_get_profile_success(db_session):
    """Getting an existing profile returns it."""
    data = {"full_name": "Jane Doe", "email": "jane@example.com"}
    created = await setup.create_profile(db_session, "test-user-id", data)
    fetched = await setup.get_profile(db_session, "test-user-id")

    assert fetched.id == created.id
    assert fetched.full_name == "Jane Doe"


@pytest.mark.asyncio
async def test_update_profile(db_session):
    """Updating a profile changes only the provided fields."""
    data = {"full_name": "Jane Doe", "email": "jane@example.com", "location": "Copenhagen"}
    await setup.create_profile(db_session, "test-user-id", data)

    updated = await setup.update_profile(db_session, "test-user-id", {"location": "Aarhus"})
    assert updated.location == "Aarhus"
    assert updated.full_name == "Jane Doe"  # unchanged
    assert updated.email == "jane@example.com"  # unchanged


@pytest.mark.asyncio
async def test_update_profile_not_found(db_session):
    """Updating a nonexistent profile raises NotFoundError."""
    with pytest.raises(NotFoundError):
        await setup.update_profile(db_session, "nonexistent", {"full_name": "X"})


@pytest.mark.asyncio
async def test_delete_profile(db_session):
    """Deleting a profile removes it."""
    data = {"full_name": "Jane Doe"}
    await setup.create_profile(db_session, "test-user-id", data)

    await setup.delete_profile(db_session, "test-user-id")

    with pytest.raises(NotFoundError):
        await setup.get_profile(db_session, "test-user-id")


@pytest.mark.asyncio
async def test_complete_setup(db_session):
    """Completing setup sets the method and timestamp."""
    data = {"full_name": "Jane Doe"}
    await setup.create_profile(db_session, "test-user-id", data)

    profile = await setup.complete_setup(db_session, "test-user-id", "documents")
    assert profile.setup_method == "documents"
    assert profile.setup_completed_at is not None


# ── Behavioral Profile Tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_behavioral_profile_create(db_session):
    """Upserting a behavioral profile creates it when it doesn't exist."""
    candidate = await setup.create_profile(db_session, "test-user-id", {"full_name": "Jane Doe"})

    bp = await setup.upsert_behavioral_profile(
        db_session,
        candidate.id,
        {"profile_type": "Analytical Driver", "summary": "Test summary"},
    )
    assert bp.profile_type == "Analytical Driver"
    assert bp.summary == "Test summary"


@pytest.mark.asyncio
async def test_upsert_behavioral_profile_update(db_session):
    """Upserting a behavioral profile updates it when it exists."""
    candidate = await setup.create_profile(db_session, "test-user-id", {"full_name": "Jane Doe"})
    await setup.upsert_behavioral_profile(db_session, candidate.id, {"profile_type": "Type A"})

    bp = await setup.upsert_behavioral_profile(db_session, candidate.id, {"summary": "Updated summary"})
    assert bp.profile_type == "Type A"  # unchanged
    assert bp.summary == "Updated summary"


# ── STAR Examples Tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_star_examples(db_session):
    """Creating STAR examples and listing them works."""
    candidate = await setup.create_profile(db_session, "test-user-id", {"full_name": "Jane Doe"})

    await setup.create_star_example(
        db_session,
        candidate.id,
        {
            "title": "ML Pipeline Optimization",
            "situation": "Slow data pipeline",
            "task": "Reduce latency",
            "action": "Rewrote batch processing",
            "result": "10x faster",
            "use_for": ["technical", "performance"],
        },
    )
    await setup.create_star_example(
        db_session,
        candidate.id,
        {
            "title": "Team Leadership",
            "situation": "Team of 5",
            "task": "Deliver project on time",
            "action": "Implemented agile",
            "result": "Shipped 2 weeks early",
        },
    )

    examples = await setup.list_star_examples(db_session, candidate.id)
    assert len(examples) == 2
    assert examples[0].title == "ML Pipeline Optimization"
    assert examples[1].title == "Team Leadership"


@pytest.mark.asyncio
async def test_delete_star_example(db_session):
    """Deleting a STAR example removes it."""
    candidate = await setup.create_profile(db_session, "test-user-id", {"full_name": "Jane Doe"})
    example = await setup.create_star_example(
        db_session,
        candidate.id,
        {
            "title": "Test Example",
            "situation": "S",
            "task": "T",
            "action": "A",
            "result": "R",
        },
    )

    await setup.delete_star_example(db_session, example.id, candidate.id)

    examples = await setup.list_star_examples(db_session, candidate.id)
    assert len(examples) == 0


@pytest.mark.asyncio
async def test_delete_star_example_wrong_owner(db_session):
    """Deleting a STAR example with wrong candidate_id raises NotFoundError."""
    candidate = await setup.create_profile(db_session, "test-user-id", {"full_name": "Jane Doe"})
    example = await setup.create_star_example(
        db_session,
        candidate.id,
        {
            "title": "Test",
            "situation": "S",
            "task": "T",
            "action": "A",
            "result": "R",
        },
    )

    with pytest.raises(NotFoundError):
        await setup.delete_star_example(db_session, example.id, "wrong-candidate-id")
