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
import logging

logger = logging.getLogger(__name__)


class QueueNotifier:
    """Lightweight event-broadcast helper for queue state changes."""

    def __init__(self):
        self._event = asyncio.Event()

    def notify(self) -> None:
        """Wake up all listeners so they can re-fetch queue status.

        Safe to call multiple times in rapid succession — if the event
        is already set, the second call is a no-op.  Listeners always
        re-read the full DB state, so intermediate updates are harmless.
        """
        self._event.set()

    async def wait_for_change(self) -> None:
        """Block until the next notification, then clear the event.

        Returns immediately if a notification arrived while we weren't
        waiting (e.g. during the previous fetch + send cycle), ensuring
        we never miss an update.
        """
        self._event.clear()
        await self._event.wait()


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
