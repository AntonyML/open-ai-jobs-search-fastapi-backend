"""APScheduler integration for periodic scraping jobs."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.session import async_session_factory
from app.services.scrape import execute_scrape

logger = logging.getLogger(__name__)

settings = get_settings()

# Global scheduler instance
scheduler: AsyncIOScheduler | None = None


async def _run_scheduled_scrape() -> None:
    """Run scheduled scrape for all users who have scrapers installed.

    This is the job that APScheduler will execute periodically.
    """
    logger.info("Starting scheduled scrape job")

    async with async_session_factory() as db:
        try:
            # Get all users who have at least one scraper installed
            # For now, we'll run for all users - in production you might want
            # to filter by users who have enabled scheduled scraping
            from app.db.models import User
            from sqlalchemy import select

            result = await db.execute(select(User.id))
            user_ids = [row[0] for row in result.all()]

            if not user_ids:
                logger.info("No users found, skipping scheduled scrape")
                return

            for user_id in user_ids:
                try:
                    logger.info(f"Running scheduled scrape for user {user_id}")
                    await execute_scrape(
                        db=db,
                        user_id=user_id,
                        triggered_by="scheduler",
                    )
                    logger.info(f"Completed scheduled scrape for user {user_id}")
                except Exception as e:
                    logger.error(f"Scheduled scrape failed for user {user_id}: {e}")
                    # Continue with other users even if one fails

        except Exception as e:
            logger.error(f"Scheduled scrape job failed: {e}")


def start_scheduler() -> AsyncIOScheduler:
    """Initialize and start the APScheduler.

    Returns:
        The started AsyncIOScheduler instance.
    """
    global scheduler

    if scheduler is not None:
        logger.warning("Scheduler already started")
        return scheduler

    scheduler = AsyncIOScheduler()

    # Add the periodic scrape job
    scheduler.add_job(
        _run_scheduled_scrape,
        trigger=IntervalTrigger(hours=settings.scrape_interval_hours),
        id="periodic_scrape",
        name="Periodic job scraping",
        replace_existing=True,
        max_instances=1,  # Prevent overlapping runs
        coalesce=True,    # If multiple runs are due, run only once
    )

    scheduler.start()
    logger.info(
        f"APScheduler started with scrape interval: {settings.scrape_interval_hours} hours"
    )
    return scheduler


def shutdown_scheduler() -> None:
    """Shutdown the APScheduler gracefully."""
    global scheduler

    if scheduler is not None:
        scheduler.shutdown(wait=True)
        scheduler = None
        logger.info("APScheduler shut down")


@asynccontextmanager
async def scheduler_lifespan() -> AsyncGenerator[None, None]:
    """Context manager for scheduler lifecycle (startup/shutdown)."""
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()