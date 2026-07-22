"""Scrape service — invokes Bun/TS scrapers via subprocess.

Discovers installed scrapers in app/external/scrapers/, runs them via
``bun run src/cli.ts search`` with the appropriate flags, parses the
JSON output, deduplicates against existing job_postings, and persists
new results to Supabase.

Designed to be called both manually (via the API) and by APScheduler
for periodic execution.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import CandidateProfile, JobPosting, ScrapeRun
from app.exceptions import ScraperError
from app.schemas.scrape import ScraperOutput, ScraperResultItem

settings = get_settings()

# ── Scraper discovery ───────────────────────────────────────────────

# Map of portal name → scraper directory name in app/external/scrapers/
PORTAL_MAP: dict[str, str] = {
    "linkedin": "linkedin-search",
    "freehire": "freehire-search",
    "jobbank": "jobbank-search",
    "jobdanmark": "jobdanmark-search",
    "jobindex": "jobindex-search",
    "jobnet": "jobnet-search",
}


def get_scraper_dir(portal: str) -> Path:
    """Return the CLI directory for a given portal."""
    folder = PORTAL_MAP.get(portal)
    if folder is None:
        raise ScraperError(f"Unknown portal: {portal}")
    return settings.scrapers_dir / folder / "cli"


def list_installed_portals() -> list[str]:
    """Return the list of portals that have a scraper installed."""
    installed = []
    for portal, folder in PORTAL_MAP.items():
        cli_dir = settings.scrapers_dir / folder / "cli"
        if cli_dir.is_dir() and (cli_dir / "src" / "cli.ts").exists():
            installed.append(portal)
    return installed


def _resolve_bun_path() -> str | None:
    """Find the bun executable path (cross-platform).

    On Windows, ``shutil.which`` returns ``bun.cmd`` (a batch wrapper),
    which ``subprocess.run`` cannot execute directly via ``CreateProcess``.
    This resolves to the underlying ``bun.exe``.
    """
    found = shutil.which("bun")
    if not found:
        return None
    if sys.platform == "win32" and found.lower().endswith(".cmd"):
        exe = Path(found).resolve().parent / "node_modules" / "bun" / "bin" / "bun.exe"
        if exe.exists():
            return str(exe)
    return found


_BUN_PATH: str | None = None


def _get_bun_path() -> str | None:
    global _BUN_PATH
    if _BUN_PATH is None:
        _BUN_PATH = _resolve_bun_path()
    return _BUN_PATH


async def check_bun_available() -> bool:
    """Check if bun is installed and on PATH."""
    return _get_bun_path() is not None


# ── Subprocess invocation ──────────────────────────────────────────


async def run_scraper(
    portal: str,
    query: str | None = None,
    jobage_days: int = 14,
    limit: int = 20,
    extra_flags: dict[str, str] | None = None,
) -> ScraperOutput:
    """Run a single scraper CLI and return parsed output.

    Args:
        portal: Portal name (e.g. "linkedin", "jobindex").
        query: Search query string.
        jobage_days: Max posting age in days.
        limit: Max results to return.
        extra_flags: Additional portal-specific flags (e.g. {"location": "Copenhagen"}).

    Returns:
        Parsed ScraperOutput with results.

    Raises:
        ScraperError: If bun is not available, the scraper fails, or output is invalid.
    """
    cli_dir = get_scraper_dir(portal)
    cli_script = cli_dir / "src" / "cli.ts"

    if not cli_script.exists():
        raise ScraperError(f"Scraper script not found: {cli_script}")

    # Build command
    bun_path = _get_bun_path()
    if not bun_path:
        raise ScraperError(
            "bun is not installed or not on PATH. Install it from https://bun.sh"
        )
    cmd = [bun_path, "run", str(cli_script), "search", "--format", "json"]

    # Query flag — different portals use different flag names
    if query:
        query_flag = _get_query_flag(portal)
        cmd.extend([query_flag, query])

    # Jobage flag
    jobage_flag = _get_jobage_flag(portal)
    if jobage_flag:
        cmd.extend([jobage_flag, str(jobage_days)])

    # Limit flag
    limit_flag = _get_limit_flag(portal)
    if limit_flag:
        cmd.extend([limit_flag, str(limit)])

    # Extra portal-specific flags
    if extra_flags:
        for flag, value in extra_flags.items():
            cmd.extend([flag, value])

    # Run subprocess
    def _run_sync() -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cli_dir),
        )

    try:
        completed = await asyncio.to_thread(_run_sync)
    except FileNotFoundError:
        raise ScraperError(
            "bun is not installed or not on PATH. Install it from https://bun.sh"
        )

    stdout = completed.stdout
    stderr = completed.stderr

    if completed.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        # Non-zero exit is logged but doesn't abort the whole scrape
        raise ScraperError(f"Scraper '{portal}' failed: {error_msg}")

    # Parse JSON output
    try:
        data = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ScraperError(f"Scraper '{portal}' produced invalid JSON: {exc}")

    return ScraperOutput.model_validate(data)


def _get_query_flag(portal: str) -> str:
    """Return the query flag name for a portal."""
    flags = {
        "linkedin": "--query",
        "freehire": "--query",
        "jobindex": "--query",
        "jobbank": "--query",
        "jobdanmark": "--text",
        "jobnet": "--search-string",
    }
    return flags.get(portal, "--query")


def _get_jobage_flag(portal: str) -> str | None:
    """Return the jobage flag name for a portal, or None if unsupported."""
    flags = {
        "linkedin": "--jobage",
        "freehire": "--jobage",
        "jobindex": "--jobage",
        "jobbank": "--jobage",
        "jobdanmark": None,  # No jobage flag
        "jobnet": None,  # No jobage flag
    }
    return flags.get(portal)


def _get_limit_flag(portal: str) -> str | None:
    """Return the limit flag name for a portal."""
    flags = {
        "linkedin": "--limit",
        "freehire": "--limit",
        "jobindex": "--limit",
        "jobbank": "--limit",
        "jobdanmark": "--limit",
        "jobnet": "--limit",
    }
    return flags.get(portal, "--limit")


# ── Deduplication ──────────────────────────────────────────────────


async def _get_existing_posting_keys(
    db: AsyncSession, user_id: str
) -> set[tuple[str, str]]:
    """Return a set of (portal, external_id) tuples already in the DB."""
    result = await db.execute(
        select(JobPosting.portal, JobPosting.external_id).where(
            JobPosting.user_id == user_id
        )
    )
    return {(row[0], row[1]) for row in result.all()}


# ── Main scrape orchestration ──────────────────────────────────────


async def execute_scrape(
    db: AsyncSession,
    user_id: str,
    focus_area: str | None = None,
    keywords: list[str] | None = None,
    target_titles: list[str] | None = None,
    seniority: str | None = None,
    location: str | None = None,
    remote: str | None = None,
    broad: bool = False,
    portals: list[str] | None = None,
    jobage_days: int = 14,
    limit_per_portal: int = 20,
    triggered_by: str = "manual",
) -> ScrapeRun:
    """Execute a full scrape run using the multi-source orchestrator.

    Delegates source selection and execution to the orchestrator, which
    decides which portals and collectors to query based on the user's
    profile and query type (specific vs broad).

    Portal results are persisted to ``job_postings``; collector results
    are stored ephemerally on the ``ScrapeRun`` record.
    """
    # ── Enrich with profile data ───────────────────────────────
    location_extra = location
    remote_flag = remote
    search_radius_km = None
    try:
        result = await db.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if profile and profile.job_target:
            jt = profile.job_target
            if not location_extra and jt.get("search_locations"):
                location_extra = jt["search_locations"][0]
            if not remote and jt.get("work_mode"):
                modes = jt["work_mode"]
                if isinstance(modes, list) and "remote" in modes:
                    remote_flag = "remote"
            if not search_radius_km and jt.get("search_radius_km"):
                search_radius_km = jt["search_radius_km"]
    except Exception:
        pass

    # ── Decide sources via orchestrator ────────────────────────
    from app.services.orchestrator import decide_sources, execute_plan

    plan = decide_sources(
        target_titles=target_titles,
        keywords=keywords,
        focus_area=focus_area,
        broad=broad,
        portals=portals,
    )

    if not plan:
        raise ScraperError("No sources selected. Install a scraper with /add-portal.")

    # ── Create scrape run record ───────────────────────────────
    portal_names = sorted(
        {p.source.name for p in plan if p.source.type == "portal"}
    )
    collector_names = sorted(
        {p.source.name for p in plan if p.source.type == "collector"}
    )
    all_sources = portal_names + collector_names

    run = ScrapeRun(
        user_id=user_id,
        triggered_by=triggered_by,
        focus_area=focus_area,
        broad=broad,
        portals_queried=portal_names or all_sources,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()

    # ── Check bun availability (if any portal in plan) ─────────
    if portal_names and not await check_bun_available():
        run.status = "failed"
        run.error_message = (
            "bun is not installed or not on PATH. "
            "Install it with: npm install -g bun"
        )
        run.completed_at = datetime.now(timezone.utc)
        await db.flush()
        await db.commit()
        raise ScraperError(
            "bun is not installed or not on PATH. "
            "Install it with: npm install -g bun"
        )

    # ── Execute the plan ───────────────────────────────────────
    existing_keys = await _get_existing_posting_keys(db, user_id)

    portal_results, collector_results, error_messages = await execute_plan(
        db=db,
        user_id=user_id,
        plan=plan,
        location_extra=location_extra,
        remote_flag=remote_flag,
        search_radius_km=search_radius_km,
        jobage_days=jobage_days,
        limit_per_source=limit_per_portal,
        existing_keys=existing_keys,
    )

    # ── Persist portal results ─────────────────────────────────
    total_found = len(portal_results) + len(collector_results)
    total_new = 0
    total_errors = len(error_messages)

    for item in portal_results:
        posting = JobPosting(
            user_id=user_id,
            portal=item["source"],
            external_id=item.get("id") or item.get("url") or item["title"],
            title=item["title"],
            company=item.get("company"),
            location=item.get("location"),
            url=item.get("url"),
            posting_date=item.get("date"),
            status="new",
            raw_data=item,
        )
        db.add(posting)
        total_new += 1

    # ── Update run record ──────────────────────────────────────
    run.jobs_found = total_found
    run.jobs_new = total_new
    run.jobs_expired = 0
    run.external_sources = collector_names or None
    run.external_results = collector_results if collector_results else None
    run.status = "completed" if total_errors == 0 else "completed_with_errors"
    if error_messages:
        run.error_message = "; ".join(error_messages)
    run.completed_at = datetime.now(timezone.utc)

    await db.flush()
    await db.commit()
    await db.refresh(run)

    return run


# ── Query job postings ─────────────────────────────────────────────


async def list_job_postings(
    db: AsyncSession,
    user_id: str,
    status_filter: str | None = None,
    portal: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JobPosting]:
    """List job postings for a user with optional filters."""
    query = select(JobPosting).where(JobPosting.user_id == user_id)

    if status_filter:
        query = query.where(JobPosting.status == status_filter)
    if portal:
        query = query.where(JobPosting.portal == portal)

    query = query.order_by(JobPosting.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_job_posting(
    db: AsyncSession, posting_id: str, user_id: str
) -> JobPosting:
    """Get a single job posting, verifying ownership."""
    from app.exceptions import NotFoundError

    result = await db.execute(
        select(JobPosting).where(
            JobPosting.id == posting_id,
            JobPosting.user_id == user_id,
        )
    )
    posting = result.scalar_one_or_none()
    if posting is None:
        raise NotFoundError("Job posting not found.")
    return posting


async def get_scrape_run(
    db: AsyncSession, run_id: str, user_id: str
) -> ScrapeRun | None:
    """Get a single scrape run by ID, verifying ownership."""
    result = await db.execute(
        select(ScrapeRun).where(
            ScrapeRun.id == run_id,
            ScrapeRun.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_scrape_runs(
    db: AsyncSession, user_id: str, limit: int = 20
) -> list[ScrapeRun]:
    """List scrape run history for a user."""
    result = await db.execute(
        select(ScrapeRun)
        .where(ScrapeRun.user_id == user_id)
        .order_by(ScrapeRun.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())