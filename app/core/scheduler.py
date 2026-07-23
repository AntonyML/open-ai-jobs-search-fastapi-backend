"""APScheduler integration for periodic scraping jobs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.settings import get_settings
from app.db.models import User
from app.db.session import async_session_factory
from app.services.scrape import execute_scrape

logger = logging.getLogger(__name__)

settings = get_settings()

# Global scheduler instance
scheduler: AsyncIOScheduler | None = None

MAX_CONCURRENT_SCRAPES = 3

# ── Lifespan helpers used by app.main ────────────────────────


@contextlib.asynccontextmanager
async def scheduler_lifespan():
    """Start the scheduler on enter, yield, then shut it down on exit."""
    start_scheduler()
    try:
        yield
    finally:
        pass  # shutdown is handled by lifespan's explicit call


def start_scheduler() -> None:
    """Create and start the global APScheduler instance."""
    global scheduler
    if scheduler is not None:
        return

    interval_hours = settings.scrape_interval_hours

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_scheduled_scrape,
        trigger="interval",
        hours=interval_hours,
        id="scrape_all_users",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started (interval=%d hours)", interval_hours)


def shutdown_scheduler() -> None:
    """Shut down the global APScheduler instance."""
    global scheduler
    if scheduler is None:
        return
    scheduler.shutdown(wait=False)
    scheduler = None
    logger.info("Scheduler shut down")


async def _scrape_user(sem: asyncio.Semaphore, user_id: str) -> None:
    """Scrape for a single user, respecting the concurrency semaphore."""
    async with sem:
        try:
            logger.info("Running scheduled scrape for user %s", user_id)
            async with async_session_factory() as db:
                await execute_scrape(db=db, user_id=user_id, triggered_by="scheduler")
            logger.info("Completed scheduled scrape for user %s", user_id)
        except Exception as e:
            logger.error("Scheduled scrape failed for user %s: %s", user_id, e)


async def _run_scheduled_scrape() -> None:
    """Run scheduled scrape for all users.

    Processes users concurrently with a semaphore to limit to
    ``MAX_CONCURRENT_SCRAPES`` simultaneous executions. Each scrape
    gets its own DB session.
    """
    logger.info("Starting scheduled scrape job")

    try:
        async with async_session_factory() as db:
            result = await db.execute(select(User.id))
            user_ids = [row[0] for row in result.all()]

        if not user_ids:
            logger.info("No users found, skipping scheduled scrape")
            return

        sem = asyncio.Semaphore(MAX_CONCURRENT_SCRAPES)
        tasks = [_scrape_user(sem, uid) for uid in user_ids]
        await asyncio.gather(*tasks)

    except Exception as e:
        logger.error("Scheduled scrape job failed: %s", e)
