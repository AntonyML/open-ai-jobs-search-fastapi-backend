"""APScheduler integration for periodic scraping jobs."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
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