"""External job source collectors.

Each collector implements ``collect()`` and returns a list of
``ScraperResultItem``, the same type used by the traditional Bun/TS scrapers.
This allows the scrape orchestrator to treat all sources uniformly.
"""

from __future__ import annotations

from pydantic import BaseModel


class ScraperResultItem(BaseModel):
    """A single result item from a scraper CLI's JSON output."""

    id: str | None = None
    title: str
    company: str | None = None
    location: str | None = None
    url: str | None = None
    date: str | None = None


class BaseCollector:
    """Base class for external source collectors."""

    name: str = ""
    label: str = ""

    async def collect(
        self,
        query: str | None = None,
        location: str | None = None,
        remote: str | None = None,
        limit: int = 20,
        **kwargs: object,
    ) -> list[ScraperResultItem]:
        """Collect job listings from the external source."""
        raise NotImplementedError


from app.services.collectors.telegram import TelegramCollector
from app.services.collectors.sheets import GoogleSheetsCollector
from app.services.collectors.rss import RSSCollector

COLLECTORS: dict[str, BaseCollector] = {
    "telegram_stem": TelegramCollector(),
    "google_sheets_stem": GoogleSheetsCollector(),
    "rss_generic": RSSCollector(),
}


def get_collector(name: str) -> BaseCollector | None:
    return COLLECTORS.get(name)


def list_collectors() -> list[dict[str, str]]:
    return [
        {"id": k, "label": v.label, "name": v.name}
        for k, v in COLLECTORS.items()
    ]
