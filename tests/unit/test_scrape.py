"""Tests for the scrape service.

Uses an in-memory SQLite database and mocks the subprocess calls to Bun/TS scrapers.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, JobPosting, ScrapeRun, User
from app.exceptions import NotFoundError
from app.services import scrape


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


# ── Helper: mock subprocess ─────────────────────────────────────────


class MockProcess:
    """Simple mock for asyncio subprocess."""
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.returncode = returncode
        self._stdout = stdout.encode() if isinstance(stdout, str) else stdout
        self._stderr = stderr.encode() if isinstance(stderr, str) else stderr
    
    def __await__(self):
        async def _await():
            return self
        return _await().__await__()
    
    async def communicate(self):
        return self._stdout, self._stderr


def mock_subprocess_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock for asyncio.create_subprocess_exec."""
    return MockProcess(stdout=stdout, stderr=stderr, returncode=returncode)


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_scrape_single_portal(db_session):
    """execute_scrape runs a single portal scraper and stores results."""
    # Mock the linkedin-search CLI output
    linkedin_output = {
        "meta": {"count": 2, "page": 1},
        "results": [
            {
                "id": "job-1",
                "title": "Senior Python Developer",
                "company": "Acme Corp",
                "location": "Copenhagen",
                "date": "2026-07-10",
                "url": "https://linkedin.com/jobs/1",
            },
            {
                "id": "job-2",
                "title": "Data Scientist",
                "company": "Beta Inc",
                "location": "Aarhus",
                "date": "2026-07-09",
                "url": "https://linkedin.com/jobs/2",
            },
        ],
    }

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.return_value = mock_subprocess_run(
            stdout=__import__("json").dumps(linkedin_output)
        )

        run = await scrape.execute_scrape(
            db=db_session,
            user_id="test-user-id",
            focus_area=None,
            broad=False,
            portals=["linkedin"],
            jobage_days=14,
            limit_per_portal=20,
            triggered_by="manual",
        )

    assert run.status == "completed"
    assert run.jobs_found == 2
    assert run.jobs_new == 2
    assert "linkedin" in run.portals_queried

    # Verify jobs were stored
    result = await db_session.execute(
        select(JobPosting).where(JobPosting.user_id == "test-user-id")
    )
    jobs = list(result.scalars().all())
    assert len(jobs) == 2
    assert jobs[0].title == "Senior Python Developer"
    assert jobs[0].portal == "linkedin"
    assert jobs[0].external_id == "job-1"


@pytest.mark.asyncio
async def test_execute_scrape_deduplicates(db_session):
    """execute_scrape deduplicates jobs by (portal, external_id)."""
    linkedin_output = {
        "meta": {"count": 1, "page": 1},
        "results": [
            {
                "id": "job-1",
                "title": "Senior Python Developer",
                "company": "Acme Corp",
                "location": "Copenhagen",
                "date": "2026-07-10",
                "url": "https://linkedin.com/jobs/1",
            }
        ],
    }

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.return_value = mock_subprocess_run(
            stdout=__import__("json").dumps(linkedin_output)
        )

        # First run
        await scrape.execute_scrape(
            db=db_session,
            user_id="test-user-id",
            portals=["linkedin"],
            triggered_by="manual",
        )

        # Second run with same job
        await scrape.execute_scrape(
            db=db_session,
            user_id="test-user-id",
            portals=["linkedin"],
            triggered_by="manual",
        )

    # Should only have 1 job
    result = await db_session.execute(
        select(JobPosting).where(JobPosting.user_id == "test-user-id")
    )
    jobs = list(result.scalars().all())
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_execute_scrape_handles_cli_error(db_session):
    """execute_scrape continues if a CLI tool fails."""
    from app.schemas.scrape import ScraperOutput, ScraperResultItem
    
    # First portal succeeds
    linkedin_output = ScraperOutput(
        meta={"count": 1, "page": 1},
        results=[
            ScraperResultItem(
                id="job-1",
                title="Python Dev",
                company="Acme",
                location="Copenhagen",
                date="2026-07-10",
                url="https://linkedin.com/jobs/1",
            )
        ],
    )
    
    # Second portal fails
    from app.exceptions import ScraperError
    
    call_count = 0
    
    async def mock_run_scraper(portal, query=None, jobage_days=14, limit=20, extra_flags=None):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return linkedin_output
        else:
            call_count += 1
            raise ScraperError(f"Scraper '{portal}' failed: API rate limited")
    
    with patch("app.services.scrape.run_scraper", side_effect=mock_run_scraper):
        with patch("app.services.scrape.check_bun_available", return_value=True):
            run = await scrape.execute_scrape(
            db=db_session,
            user_id="test-user-id",
            portals=["linkedin", "jobindex"],
            triggered_by="manual",
        )

    assert run.status == "completed_with_errors"
    assert run.jobs_found == 1
    assert "linkedin" in run.portals_queried
    assert "jobindex" in run.portals_queried


@pytest.mark.asyncio
async def test_list_scrape_runs(db_session):
    """list_scrape_runs returns runs for the user."""
    # Create some runs
    for i in range(3):
        run = ScrapeRun(
            user_id="test-user-id",
            triggered_by="manual",
            portals_queried=["linkedin"],
            jobs_found=1,
            jobs_new=1,
            status="completed",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        db_session.add(run)
    await db_session.commit()

    runs = await scrape.list_scrape_runs(db_session, "test-user-id", limit=10)
    assert len(runs) == 3


@pytest.mark.asyncio
async def test_list_job_postings(db_session):
    """list_job_postings returns jobs with filters."""
    # Create jobs
    for i in range(3):
        job = JobPosting(
            user_id="test-user-id",
            portal="linkedin",
            external_id=f"job-{i}",
            title=f"Job {i}",
            company="Acme",
            location="Copenhagen",
            status="new" if i < 2 else "ranked",
        )
        db_session.add(job)
    await db_session.commit()

    # All jobs
    jobs = await scrape.list_job_postings(db_session, "test-user-id", limit=10)
    assert len(jobs) == 3

    # Filter by status
    jobs = await scrape.list_job_postings(
        db_session, "test-user-id", status_filter="new", limit=10
    )
    assert len(jobs) == 2

    # Filter by portal
    jobs = await scrape.list_job_postings(
        db_session, "test-user-id", portal="linkedin", limit=10
    )
    assert len(jobs) == 3


@pytest.mark.asyncio
async def test_get_job_posting(db_session):
    """get_job_posting returns a single job."""
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-1",
        title="Python Developer",
        company="Acme",
        location="Copenhagen",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    fetched = await scrape.get_job_posting(db_session, job.id, "test-user-id")
    assert fetched.id == job.id
    assert fetched.title == "Python Developer"


@pytest.mark.asyncio
async def test_get_job_posting_not_found(db_session):
    """get_job_posting raises NotFoundError for missing job."""
    with pytest.raises(NotFoundError):
        await scrape.get_job_posting(db_session, "nonexistent-id", "test-user-id")


@pytest.mark.asyncio
async def test_get_job_posting_wrong_user(db_session):
    """get_job_posting raises NotFoundError if job belongs to another user."""
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-1",
        title="Python Developer",
    )
    db_session.add(job)
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await scrape.get_job_posting(db_session, job.id, "other-user-id")
