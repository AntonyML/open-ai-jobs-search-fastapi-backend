"""Google Sheets collector — reads public sheets as a job source.

Reads a published Google Sheet by exporting to CSV. Each row is treated
as a potential job posting. Columns are auto-detected by header names
(keywords: title, company, location, url, date, description).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone

import httpx

from app.services.collectors import BaseCollector, ScraperResultItem

# Public sheets to monitor — add more as needed
SHEET_URLS: list[str] = [
    "https://docs.google.com/spreadsheets/d/1wl7edAy6TcVuFh13LQ4FtvCZgPUb3ETFcQp8jlfuanM/gviz/tq?tqx=out:csv&gid=0",
]

# Column name synonyms for auto-detection
COL_TITLE = re.compile(r"title|puesto|cargo|rol|position|job", re.I)
COL_COMPANY = re.compile(r"company|empresa|compania|organization", re.I)
COL_LOCATION = re.compile(r"location|ubicacion|lugar|site|place", re.I)
COL_URL = re.compile(r"url|link|enlace|apply|postular", re.I)
COL_DATE = re.compile(r"date|fecha|posted|publicado|created", re.I)
COL_DESC = re.compile(r"description|descripcion|details|detalle|notes", re.I)


class GoogleSheetsCollector(BaseCollector):
    name = "google_sheets_stem"
    label = "Google Sheets — Bolsas STEM Costa Rica"

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

        for sheet_url in SHEET_URLS:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(sheet_url)
                    resp.raise_for_status()
            except Exception:
                continue

            reader = csv.DictReader(io.StringIO(resp.text))
            if not reader.fieldnames:
                continue

            # Auto-detect columns
            col_map = self._detect_columns(reader.fieldnames)

            for row in reader:
                title = self._val(row, col_map, "title")
                if not title:
                    continue
                if query_lower and query_lower not in title.lower():
                    continue

                company = self._val(row, col_map, "company")
                loc = self._val(row, col_map, "location")
                url = self._val(row, col_map, "url")
                date_raw = self._val(row, col_map, "date")

                results.append(
                    ScraperResultItem(
                        id=f"gs_{hash(title + (company or '')) & 0xFFFFFFFF:08x}",
                        title=title,
                        company=company,
                        location=loc,
                        url=url,
                        date=date_raw,
                    )
                )

                if len(results) >= limit:
                    break

        return results

    def _detect_columns(
        self, headers: list[str]
    ) -> dict[str, str]:
        """Map standard field names to actual column headers."""
        mapping: dict[str, str] = {}
        for h in headers:
            h_lower = h.lower().strip()
            if COL_TITLE.search(h_lower):
                mapping["title"] = h
            elif COL_COMPANY.search(h_lower):
                mapping["company"] = h
            elif COL_LOCATION.search(h_lower):
                mapping["location"] = h
            elif COL_URL.search(h_lower):
                mapping["url"] = h
            elif COL_DATE.search(h_lower):
                mapping["date"] = h
            elif COL_DESC.search(h_lower):
                mapping["description"] = h
        return mapping

    def _val(
        self, row: dict[str, str], col_map: dict[str, str], field: str
    ) -> str | None:
        key = col_map.get(field)
        if not key:
            return None
        val = row.get(key, "").strip()
        return val or None
