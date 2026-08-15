"""Tests for the job data reset service (DELETE /job-data).

Verifies that executing a reset purges the on-disk apply artifacts via
``artifact_store`` while preserving the CV Builder workspace, and that the
tracker CSV is deleted.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import pytest

from app.db.models import Base, CandidateProfile, GeneratedCV, JobPosting, User
from app.services import artifact_store
from app.services.job_data import execute_job_data


@pytest.fixture
async def db_session():
    """In-memory SQLite DB with a test user."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                id="test-user-id",
                email="test@example.com",
                hashed_password="fakehash",
                full_name="Test User",
            )
        )
        session.add(
            JobPosting(
                id="job-post-1",
                user_id="test-user-id",
                portal="linkedin",
                external_id="ext-1",
                title="Software Engineer",
            )
        )
        session.add(
            GeneratedCV(
                id="cv-record-1",
                user_id="test-user-id",
                cv_type="base",
                cv_json={"first_name": "Jane", "last_name": "Doe"},
                is_deleted=False,
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
def artifact_settings(monkeypatch, tmp_path):
    """Anchors artifact storage + tracker to a temp dir."""
    monkeypatch.chdir(tmp_path)

    class _Settings:
        cv_storage_path = "generated_cvs"
        generated_storage_path = "generated"
        tracker_path = str(tmp_path / "documents" / "tracker.json")

    monkeypatch.setattr(artifact_store, "get_settings", lambda: _Settings())
    monkeypatch.setattr("app.core.settings.get_settings", lambda: _Settings())
    return _Settings()


async def test_reset_purges_apply_artifacts_on_disk(db_session, artifact_settings):
    # Apply pipeline left files on disk under generated/<user_id>/...
    abs_path, _ = artifact_store.new_output_path(
        "apply", "test-user-id", "job-post-1", "cv_job.pdf"
    )
    abs_path.write_bytes(b"%PDF-1.4")

    summary = await execute_job_data(db_session, "test-user-id")

    assert abs_path.exists() is False
    assert summary["deleted"].get("apply_artifacts_purged") == 1


async def test_reset_preserves_cv_builder_workspace(db_session, artifact_settings):
    # CV Builder artifacts (generated_cvs/) are user workspace, NOT job data.
    cv_abs, _ = artifact_store.new_output_path("cv", "test-user-id", "cv-record-1.pdf")
    cv_abs.write_bytes(b"%PDF-1.5")

    summary = await execute_job_data(db_session, "test-user-id")

    assert cv_abs.exists() is True
    assert "cv_artifacts" not in summary["deleted"]


async def test_reset_removes_tracker_csv(db_session, artifact_settings):
    tracker = artifact_settings.tracker_path
    from pathlib import Path

    Path(tracker).parent.mkdir(parents=True, exist_ok=True)
    Path(tracker).write_text("applied\n", encoding="utf-8")

    summary = await execute_job_data(db_session, "test-user-id")

    assert Path(tracker).exists() is False
    assert summary["deleted"].get("tracker_csv") == 1


async def test_reset_deletes_job_postings_and_preserves_user(
    db_session, artifact_settings
):
    summary = await execute_job_data(db_session, "test-user-id")

    assert summary["deleted"]["job_postings"] == 1
    rows = (await db_session.execute(select(JobPosting))).scalars().all()
    assert rows == []
    user = (await db_session.execute(select(User))).scalar_one_or_none()
    assert user is not None