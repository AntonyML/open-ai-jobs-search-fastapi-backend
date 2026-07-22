"""Multi-source scraping router.

Decides which sources to query based on the user's profile and query type,
then executes them in parallel, deduplicates, and consolidates results.

Source types:
  - **portal**: traditional Bun/TS scraper (results persisted to DB)
  - **collector**: Python collector (ephemeral results, not persisted)

Distribution strategy:
  - Specific queries (``target_titles`` present): LinkedIn + specialist portals
  - Broad queries (only ``keywords`` / ``focus_area``): external collectors + general portals
  - External collectors always run (cached with TTL)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScrapeRun
from app.exceptions import ScraperError
from app.services.cache import scrape_cache
from app.services.collectors import get_collector

# ── Source registry ─────────────────────────────────────────────────

COOLDOWN_DEFAULT_HOURS = 4


@dataclass
class SourceDef:
    """Registered source (portal or collector)."""

    name: str
    type: str  # "portal" | "collector"
    strength: str  # "specific" | "broad" | "location"
    cooldown_hours: int = COOLDOWN_DEFAULT_HOURS
    priority: int = 10  # lower = preferred
    cache_ttl_hours: int = 0  # 0 = no cache


SOURCE_REGISTRY: dict[str, SourceDef] = {
    "linkedin": SourceDef(
        name="linkedin", type="portal", strength="specific",
        cooldown_hours=6, priority=1,
    ),
    "freehire": SourceDef(
        name="freehire", type="portal", strength="specific",
        cooldown_hours=4, priority=2,
    ),
    "jobindex": SourceDef(
        name="jobindex", type="portal", strength="specific",
        cooldown_hours=4, priority=2,
    ),
    "jobbank": SourceDef(
        name="jobbank", type="portal", strength="broad",
        cooldown_hours=4, priority=3,
    ),
    "jobdanmark": SourceDef(
        name="jobdanmark", type="portal", strength="broad",
        cooldown_hours=4, priority=3,
    ),
    "jobnet": SourceDef(
        name="jobnet", type="portal", strength="broad",
        cooldown_hours=4, priority=3,
    ),
    "telegram_stem": SourceDef(
        name="telegram_stem", type="collector", strength="broad",
        cooldown_hours=2, priority=4, cache_ttl_hours=2,
    ),
    "google_sheets_stem": SourceDef(
        name="google_sheets_stem", type="collector", strength="broad",
        cooldown_hours=4, priority=4, cache_ttl_hours=4,
    ),
    "rss_generic": SourceDef(
        name="rss_generic", type="collector", strength="broad",
        cooldown_hours=2, priority=4, cache_ttl_hours=2,
    ),
}


def get_installed_portal_names() -> list[str]:
    from app.services.scrape import list_installed_portals
    return list_installed_portals()


@dataclass
class SourcePlan:
    source: SourceDef
    queries: list[str]
    extra_flags: dict[str, str] = field(default_factory=dict)


def decide_sources(
    target_titles: list[str] | None = None,
    keywords: list[str] | None = None,
    focus_area: str | None = None,
    broad: bool = False,
    portals: list[str] | None = None,
) -> list[SourcePlan]:
    installed = get_installed_portal_names()
    plans: list[SourcePlan] = []

    specific_queries: list[str] = []
    broad_queries: list[str] = []

    if target_titles:
        kw_groups = []
        if keywords:
            kw_groups = [keywords[i : i + 3] for i in range(0, len(keywords), 3)]
        for i, title in enumerate(target_titles):
            if kw_groups and i < len(kw_groups):
                specific_queries.append(f"{title} {' '.join(kw_groups[i])}")
            else:
                specific_queries.append(title)

    if not specific_queries and keywords:
        kw_groups = [keywords[i : i + 3] for i in range(0, len(keywords), 3)]
        broad_queries.extend(" ".join(g) for g in kw_groups)

    if not specific_queries and not broad_queries and focus_area:
        words = focus_area.split()
        if len(words) > 5:
            broad_queries.append(" ".join(words[:3]))
            for i in range(3, len(words), 3):
                broad_queries.append(" ".join(words[i : i + 3]))
        else:
            broad_queries.append(focus_area)

    if not specific_queries and not broad_queries:
        broad_queries.append("")

    if portals is not None:
        for p in portals:
            if p not in installed:
                continue
            sd = SOURCE_REGISTRY[p]
            qs = specific_queries if sd.strength == "specific" else broad_queries
            plans.append(SourcePlan(source=sd, queries=qs or [""]))
        return plans

    has_specific = bool(specific_queries)
    has_broad_only = bool(broad_queries) and not has_specific

    for name, sd in sorted(
        SOURCE_REGISTRY.items(), key=lambda x: (x[1].priority, x[1].name)
    ):
        if sd.type == "portal" and name not in installed:
            continue

        if has_specific:
            if sd.strength == "specific" and sd.type == "portal":
                plans.append(SourcePlan(source=sd, queries=specific_queries))
            elif sd.strength == "broad" and broad:
                plans.append(SourcePlan(source=sd, queries=broad_queries or [""]))
            elif sd.type == "collector":
                plans.append(SourcePlan(source=sd, queries=broad_queries or [""]))
        else:
            if sd.type == "portal":
                if sd.strength == "specific" and has_broad_only and broad:
                    plans.append(SourcePlan(source=sd, queries=broad_queries))
                elif sd.strength in ("broad", "specific") and (
                    broad or sd.strength == "specific"
                ):
                    qs = specific_queries or broad_queries or [""]
                    plans.append(SourcePlan(source=sd, queries=qs))
                elif sd.strength == "broad":
                    plans.append(SourcePlan(source=sd, queries=broad_queries or [""]))
            else:
                plans.append(SourcePlan(source=sd, queries=broad_queries or [""]))

    seen: set[str] = set()
    deduped: list[SourcePlan] = []
    for p in plans:
        if p.source.name not in seen:
            seen.add(p.source.name)
            deduped.append(p)
    return deduped


async def _get_recent_run_times(
    db: AsyncSession, user_id: str, hours: int
) -> dict[str, datetime]:
    cutoff = datetime.now(timezone.utc)
    result = await db.execute(
        select(ScrapeRun.portals_queried, ScrapeRun.completed_at)
        .where(
            ScrapeRun.user_id == user_id,
            ScrapeRun.status.in_(["completed", "completed_with_errors"]),
            ScrapeRun.completed_at >= cutoff,
        )
        .order_by(ScrapeRun.completed_at.desc())
        .limit(50)
    )
    last_run: dict[str, datetime] = {}
    for portals_queried, completed_at in result.all():
        if completed_at is None:
            continue
        for p in portals_queried or []:
            if p not in last_run:
                last_run[p] = completed_at
    return last_run


def _is_on_cooldown(
    sd: SourceDef, last_run: dict[str, datetime], now: datetime
) -> bool:
    if sd.cooldown_hours <= 0:
        return False
    last = last_run.get(sd.name)
    if last is None:
        return False
    elapsed = (now - last).total_seconds() / 3600
    return elapsed < sd.cooldown_hours


async def execute_plan(
    db: AsyncSession,
    user_id: str,
    plan: list[SourcePlan],
    location_extra: str | None = None,
    remote_flag: str | None = None,
    search_radius_km: int | None = None,
    jobage_days: int = 14,
    limit_per_source: int = 20,
    existing_keys: set[tuple[str, str]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    from app.services.scrape import run_scraper, get_scraper_dir

    portal_results: list[dict[str, Any]] = []
    collector_results: list[dict[str, Any]] = []
    error_messages: list[str] = []
    seen_keys: set[tuple[str, str]] = set(existing_keys or ())
    tasks: list[tuple[str, str, asyncio.Task]] = []

    now = datetime.now(timezone.utc)
    max_cooldown = max(
        (p.source.cooldown_hours for p in plan if p.source.type == "portal"),
        default=4,
    )
    last_run = await _get_recent_run_times(db, user_id, max_cooldown)

    for sp in plan:
        sd = sp.source

        if _is_on_cooldown(sd, last_run, now):
            error_messages.append(f"{sd.name}: skipped (cooldown {sd.cooldown_hours}h)")
            continue

        if sd.type == "portal":
            try:
                get_scraper_dir(sd.name)
            except ScraperError:
                error_messages.append(f"{sd.name}: scraper not installed")
                continue

            extra_flags = {}
            if location_extra and sd.name in ("linkedin", "freehire"):
                extra_flags["--location"] = location_extra
            if remote_flag and sd.name == "linkedin":
                extra_flags["--remote"] = remote_flag
            if search_radius_km:
                extra_flags["--radius"] = str(search_radius_km)

            for sq in sp.queries:
                task = asyncio.create_task(
                    run_scraper(
                        portal=sd.name,
                        query=sq,
                        jobage_days=jobage_days,
                        limit=limit_per_source,
                        extra_flags=extra_flags or None,
                    )
                )
                tasks.append((sd.name, sq, task))

        elif sd.type == "collector":
            collector = get_collector(sd.name)
            if collector is None:
                error_messages.append(f"{sd.name}: collector not available")
                continue

            for sq in sp.queries:
                if sd.cache_ttl_hours > 0:
                    cached = scrape_cache.get(sd.name, sq)
                    if cached is not None:
                        for item in cached:
                            key = (sd.name, item.get("id") or item.get("url") or item.get("title", ""))
                            if key not in seen_keys:
                                seen_keys.add(key)
                                collector_results.append(item)
                        continue

                task = asyncio.create_task(
                    collector.collect(
                        query=sq,
                        location=location_extra,
                        remote=remote_flag,
                        limit=limit_per_source,
                    )
                )
                tasks.append((sd.name, sq, task))

    if not tasks:
        return portal_results, collector_results, error_messages

    raw_results = await asyncio.gather(
        *[t for _, _, t in tasks],
        return_exceptions=True,
    )

    for (src_name, sq, _), result in zip(tasks, raw_results):
        if isinstance(result, Exception):
            error_messages.append(f"{src_name} ({sq}): {result}")
            continue

        sd = SOURCE_REGISTRY[src_name]

        if sd.type == "portal":
            items = result.results if hasattr(result, "results") else []
            for item in items:
                key = (src_name, item.id or item.url or item.title)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                portal_results.append({
                    "source": src_name,
                    "title": item.title,
                    "company": item.company,
                    "location": item.location,
                    "url": item.url,
                    "date": item.date,
                    "id": item.id,
                })
        elif sd.type == "collector":
            items = result if isinstance(result, list) else []
            cache_list: list[dict[str, Any]] = []
            for item in items:
                key = (src_name, item.id or item.url or item.title)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                entry = {
                    "source": src_name,
                    "title": item.title,
                    "company": item.company,
                    "location": item.location,
                    "url": item.url,
                    "date": item.date,
                }
                collector_results.append(entry)
                cache_list.append(entry)
            if cache_list and sd.cache_ttl_hours > 0:
                scrape_cache.set(src_name, sq, cache_list, sd.cache_ttl_hours)

    return portal_results, collector_results, error_messages
