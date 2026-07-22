"""Telegram channel collector — reads public channel messages via web view.

Scrapes the public ``t.me/s/STEMJobsLATAM`` page and parses job posts.
No Telegram API credentials required (public web view).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from app.services.collectors import BaseCollector, ScraperResultItem

# Public channels to monitor — add more as needed
TELEGRAM_CHANNELS: dict[str, str] = {
    "STEMJobsLATAM": "https://t.me/s/STEMJobsLATAM",
}

# Patterns to detect job posts (Spanish + English)
JOB_PATTERNS = re.compile(
    r"(se solicita|se busca|contratamos|estamos buscando|buscamos|"
    r"vacante|oferta(?!\s*de\s*canal)|empleo|trabajo|hiring|"
    r"we\s+are\s+hiring|job\s+opening|position|opening|"
    r"remote|remoto|senior|junior|mid)",
    re.IGNORECASE,
)


class TelegramCollector(BaseCollector):
    name = "telegram_stem"
    label = "Telegram — STEM Jobs LATAM"

    async def collect(
        self,
        query: str | None = None,
        location: str | None = None,
        remote: str | None = None,
        limit: int = 20,
        **kwargs: object,
    ) -> list[ScraperResultItem]:
        results: list[ScraperResultItem] = []
        query_lower = query.lower() if query else ""

        for channel_name, url in TELEGRAM_CHANNELS.items():
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
            except Exception:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            messages = soup.select(".tgme_widget_message_wrap")

            for msg in messages:
                text_elem = msg.select_one(".tgme_widget_message_text")
                if not text_elem:
                    continue
                text = text_elem.get_text(separator=" ", strip=True)
                if not text or len(text) < 30:
                    continue

                # Skip non-job messages
                if query_lower:
                    if query_lower not in text.lower():
                        continue
                elif not JOB_PATTERNS.search(text):
                    continue

                # Extract date
                date_elem = msg.select_one(
                    ".tgme_widget_message_date time"
                )
                date_str = date_elem.get("datetime") if date_elem else None

                # Extract company (first line before common separators)
                lines = text.split("\n")
                first_line = lines[0].strip().rstrip(":")
                company = first_line if len(first_line) < 60 else None

                # Truncate long messages for title
                title = text[:80].rsplit(" ", 1)[0] + "…" if len(text) > 80 else text

                results.append(
                    ScraperResultItem(
                        id=f"tg_{channel_name}_{hash(text) & 0xFFFFFFFF:08x}",
                        title=title,
                        company=company,
                        location=None,
                        url=f"https://t.me/{channel_name}",
                        date=date_str,
                    )
                )

                if len(results) >= limit:
                    break

        return results
