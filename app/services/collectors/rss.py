"""RSS / Atom feed collector — generic feed reader.

Users can register custom RSS feeds (e.g. from company career pages,
job boards, etc.). The collector fetches entries and parses them
into uniform job result items.
"""

from __future__ import annotations

import feedparser

from app.services.collectors import BaseCollector, ScraperResultItem

# Default RSS feeds — replace with user-configurable list
DEFAULT_FEEDS: list[str] = [
    "https://stackoverflow.com/jobs/feed",
    "https://remoteok.com/rss",
    "https://weworkremotely.com/remote-jobs.rss",
]

# Overridable at runtime via ScrapeRequest.rss_feeds
USER_FEEDS: list[str] = []


class RSSCollector(BaseCollector):
    name = "rss_generic"
    label = "RSS / Atom Feeds"

    async def collect(
        self,
        query: str | None = None,
        location: str | None = None,
        remote: str | None = None,
        limit: int = 20,
        rss_feeds: list[str] | None = None,
    ) -> list[ScraperResultItem]:
        results: list[ScraperResultItem] = []
        query_lower = query.lower() if query else ""

        feeds = rss_feeds or USER_FEEDS or DEFAULT_FEEDS

        for url in feeds:
            try:
                parsed = feedparser.parse(url)
            except Exception:
                continue

            for entry in parsed.entries:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                if query_lower and query_lower not in title.lower():
                    continue

                company = None
                if hasattr(entry, "author") and entry.author:
                    company = entry.author.strip()

                # Try to extract company from summary
                summary = entry.get("summary", "")

                location_str = self._extract_location(summary)
                location_str = location_str or entry.get("location")

                link = entry.get("link", "")
                date_raw = entry.get("published", entry.get("updated", ""))

                results.append(
                    ScraperResultItem(
                        id=f"rss_{hash(link or title) & 0xFFFFFFFF:08x}",
                        title=title[:200],
                        company=company,
                        location=location_str,
                        url=link,
                        date=date_raw,
                    )
                )

                if len(results) >= limit:
                    break

        return results

    def _extract_location(self, text: str) -> str | None:
        """Naively extract location from HTML or plain text summary."""
        if not text:
            return None
        import re

        # Common location patterns: "Location: San José, Costa Rica"
        m = re.search(
            r"(?:location|ubicaci[óo]n|place|lugar)\s*[:;]\s*([^\n.,;]{2,60})",
            text,
            re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
        return None
