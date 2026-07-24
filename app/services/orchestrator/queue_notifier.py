"""QueueNotifier — async event system for broadcasting queue state changes.

The ExecutionQueue calls ``notify()`` after every state-changing operation
(enqueue, start, complete, fail, cancel, etc.).  WebSocket handlers call
``wait_for_change()`` to sleep until the next update, then re-fetch the
full queue status and push it to the connected client.

This decouples the queue from WebSocket code — the queue never knows
who (if anyone) is listening.
"""

from __future__ import annotations

import asyncio

from datetime import datetime, timedelta
from typing import Any
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueueNotifier:
    """Lightweight event-broadcast helper for queue state changes.

    Also manages an in-memory cache of ``get_queue_status`` results per user.
    The cache is invalidated whenever ``notify()`` is called, so the next
    REST poll after a queue change will re-fetch from the DB.
    """

    def __init__(self):
        self._event = asyncio.Event()
        self._cache: dict[str, tuple[datetime, Any]] = {}
        self._cache_ttl = timedelta(seconds=1)

    def notify(self) -> None:
        """Wake up all listeners and invalidate the queue status cache."""
        self._event.set()
        self._cache.clear()

    async def wait_for_change(self) -> None:
        """Block until the next notification, then clear the event.

        Returns immediately if a notification arrived while we weren't
        waiting (e.g. during the previous fetch + send cycle), ensuring
        we never miss an update.
        """
        self._event.clear()
        await self._event.wait()

    # ── Cache helpers ──────────────────────────────────────────────

    def get_cached(self, user_id: str) -> Any | None:
        """Return cached queue status for *user_id* if still fresh."""
        entry = self._cache.get(user_id)
        if entry is None:
            return None
        cached_at, data = entry
        if datetime.utcnow() - cached_at > self._cache_ttl:
            del self._cache[user_id]
            return None
        return data

    def set_cached(self, user_id: str, data: Any) -> None:
        """Store queue status for *user_id* in memory."""
        self._cache[user_id] = (datetime.utcnow(), data)

    def clear_cache(self) -> None:
        """Drop all cached entries."""
        self._cache.clear()


# ═══════════════════════════════════════════════════════════════════
# Singleton  — shared by the ExecutionQueue and all WebSocket handlers
# ═══════════════════════════════════════════════════════════════════

_queue_notifier: QueueNotifier | None = None


def get_queue_notifier() -> QueueNotifier:
    """Return the singleton QueueNotifier instance."""
    global _queue_notifier
    if _queue_notifier is None:
        _queue_notifier = QueueNotifier()
    return _queue_notifier
