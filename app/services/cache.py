"""In-memory TTL cache for external collector results.

Simple dict-based cache with time-to-live per key.  Not persisted across
restarts — for production, swap in Redis.  Keys are ``source:query_hash``
strings.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Hashable
from typing import Any


class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl_seconds: int) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl_seconds

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class ScrapeCache:
    """Thread-safe-ish in-memory cache with TTL per entry."""

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}

    def _key(self, source: str, query: str) -> str:
        h = hashlib.md5(query.encode()).hexdigest()[:12]
        return f"{source}:{h}"

    def get(self, source: str, query: str) -> list[dict[str, Any]] | None:
        entry = self._store.get(self._key(source, query))
        if entry is None or entry.expired:
            return None
        return entry.value

    def set(
        self, source: str, query: str, value: list[dict[str, Any]], ttl_hours: int
    ) -> None:
        self._store[self._key(source, query)] = _CacheEntry(
            value, ttl_seconds=ttl_hours * 3600
        )

    def invalidate(self, source: str | None = None) -> None:
        if source:
            self._store = {
                k: v for k, v in self._store.items() if not k.startswith(f"{source}:")
            }
        else:
            self._store.clear()

    @property
    def size(self) -> int:
        _now = time.monotonic()
        # Purge on read
        self._store = {k: v for k, v in self._store.items() if not v.expired}
        return len(self._store)


# Global singleton
scrape_cache = ScrapeCache()
