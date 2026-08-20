"""Background task manager — tracks asyncio tasks for graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine

logger = logging.getLogger(__name__)

_SHUTDOWN_TIMEOUT = 30  # seconds to wait for tasks before cancelling


class BackgroundTaskManager:
    """Tracks background tasks so they can be awaited on shutdown."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def create_task(self, coro: Coroutine, *, name: str | None = None) -> asyncio.Task:
        """Create a tracked background task."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        logger.debug("Background task started: %s", name or coro.__name__)
        return task

    async def shutdown(self) -> None:
        """Wait for all tracked tasks to finish, then cancel remaining."""
        if not self._tasks:
            return
        logger.info("Waiting for %d background tasks...", len(self._tasks))
        done, pending = await asyncio.wait(
            self._tasks,
            timeout=_SHUTDOWN_TIMEOUT,
        )
        if pending:
            logger.warning(
                "Cancelling %d background tasks that did not finish in %ds",
                len(pending),
                _SHUTDOWN_TIMEOUT,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()


background_tasks = BackgroundTaskManager()
