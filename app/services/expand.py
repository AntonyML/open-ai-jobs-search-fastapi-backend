"""Expand service — discovers hidden competencies from documents and online presence.

Implements the /expand workflow:
1. Scans all available sources for "experience items" (CV, LinkedIn, diplomas, references, GitHub, other URLs)
2. Enriches each item with competencies via web search (inference + direct lookup)
3. Proposes profile additions (additive only — never modifies existing content)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, bind_context
from app.core.settings import get_settings
from app.db.models import (
    CandidateProfile,
    CompetencyExpansion,
    User,
)
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.schemas.expand import (
    EnrichedCompetenciesLLMOutput,
    ExpandRequest,  # noqa: F401 — re-export for tests/callers
    ProposedAdditionsLLMOutput,
)
from app.services import credits
from app.services.apply import _get_pdf_page_count  # noqa: F401 — re-export for tests
from app.services.orchestrator.orchestrator_deps import get_orchestrator

logger = get_logger(__name__)

settings = get_settings()

EXPAND_DOCS_DIR = Path("documents")


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def get_expansion(db: AsyncSession, expansion_id: str, user_id: str) -> CompetencyExpansion:
    """Get a competency expansion by ID, scoped to the user."""
    result = await db.execute(
        select(CompetencyExpansion).where(
            CompetencyExpansion.id == expansion_id,
            CompetencyExpansion.user_id == user_id,
        )
    )
    expansion = result.scalar_one_or_none()
    if expansion is None:
        raise NotFoundError(f"Competency expansion {expansion_id} not found")
    return expansion


async def list_expansions(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[CompetencyExpansion]:
    """List all competency expansions for a user."""
    result = await db.execute(
        select(CompetencyExpansion)
        .where(CompetencyExpansion.user_id == user_id)
        .order_by(CompetencyExpansion.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


# ── Main entry point ──────────────────────────────────────────────────────────


async def execute_expand(
    db: AsyncSession,
    user_id: str,
    scan_cv: bool = True,
    scan_linkedin: bool = True,
    scan_diplomas: bool = True,
    scan_references: bool = True,
    scan_github: bool = True,
    scan_other_urls: bool = True,
    candidate: CandidateProfile | None = None,
    usage: dict | None = None,
    correlation_id: str | None = None,
) -> CompetencyExpansion:
    """Run a full competency expansion synchronously.

    Scans all configured sources, enriches via LLM, and proposes additions.
    ``usage`` (optional) is a sink dict accumulating real token/cost usage
    from the orchestrator LLM calls.  When ``correlation_id`` is provided the
    actual usage is recorded onto the credit ledger row created by the gate.
    """
    with bind_context(stage="expand"):
        if usage is None:
            usage = {}

        if candidate is None:
            result = await db.execute(
                select(CandidateProfile).where(CandidateProfile.user_id == user_id)
            )
            candidate = result.scalar_one_or_none()

        if candidate is None:
            raise ProfileIncompleteError("Candidate profile not found. Run /setup first.")

        expansion = CompetencyExpansion(
            user_id=user_id,
            candidate_id=candidate.id,
            scanned_cv=scan_cv,
            scanned_linkedin=scan_linkedin,
            scanned_diplomas=scan_diplomas,
            scanned_references=scan_references,
            scanned_github=scan_github,
            scanned_other_urls=scan_other_urls,
            status="processing",
        )
        db.add(expansion)
        await db.commit()
        await db.refresh(expansion)

        try:
            experience_items: list[dict[str, Any]] = []

            if scan_cv:
                experience_items.extend(_scan_cv_folder())
            if scan_linkedin:
                experience_items.extend(_scan_linkedin_folder())
            if scan_diplomas:
                experience_items.extend(_scan_diplomas_folder())
            if scan_references:
                experience_items.extend(_scan_references_folder())
            if scan_github and candidate.github_url:
                repos = fetch_github_repos(candidate.github_url)
                experience_items.extend(_make_github_items(repos, candidate.github_url))
            if scan_other_urls:
                experience_items.extend(_scan_other_urls(candidate))

            expansion.experience_items = experience_items
            await db.commit()

            if not experience_items:
                expansion.status = "completed"
                expansion.enriched_competencies = []
                expansion.proposed_additions = []
                await db.commit()
                await db.refresh(expansion)
                return expansion

            orchestrator = get_orchestrator()

            enrichment_messages = build_competency_enrichment_prompt(experience_items)
            enriched_result = await orchestrator.execute(
                user_id=user_id,
                messages=enrichment_messages,
                output_schema=EnrichedCompetenciesLLMOutput,
                pipeline="expand",
                description="Competency enrichment",
                temperature=0.2,
                usage=usage,
            )

            enriched_list = [
                {
                    "experience_item_id": e.experience_item_id,
                    "competencies": e.competencies,
                    "source": e.source,
                    "source_urls": e.source_urls,
                }
                for e in enriched_result.enrichments
            ]
            expansion.enriched_competencies = enriched_list
            await db.commit()

            proposed_messages = build_proposed_additions_prompt(candidate, enriched_result.enrichments)
            proposed_result = await orchestrator.execute(
                user_id=user_id,
                messages=proposed_messages,
                output_schema=ProposedAdditionsLLMOutput,
                pipeline="expand",
                description="Proposed additions",
                temperature=0.2,
                usage=usage,
            )

            proposed_list = [
                {
                    "category": a.category,
                    "item": a.item if isinstance(a.item, dict) else {"skill": a.item},
                    "reason": a.reason,
                }
                for a in proposed_result.additions
            ]
            expansion.proposed_additions = proposed_list
            expansion.status = "completed"
            await db.commit()

            if correlation_id and usage.get("tokens_input"):
                await credits.record_llm_usage(
                    db,
                    correlation_id=correlation_id,
                    model_used=usage.get("model_used"),
                    tokens_input=usage.get("tokens_input", 0),
                    tokens_output=usage.get("tokens_output", 0),
                    cost_usd_cents=usage.get("cost_usd_cents", 0),
                )
                await db.commit()

        except Exception as exc:
            expansion.status = "failed"
            expansion.error_message = str(exc)
            await db.commit()
            raise

        await db.refresh(expansion)
        return expansion


async def _execute_expand_background(
    expansion_id: str,
    correlation_id: str | None = None,
) -> None:
    """Run expand as a background task (called from API)."""
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(
            select(CompetencyExpansion).where(CompetencyExpansion.id == expansion_id)
        )
        expansion = result.scalar_one_or_none()
        if expansion is None:
            logger.error("Expansion %s not found for background task", expansion_id)
            return

        try:
            await execute_expand(
                db=db,
                user_id=expansion.user_id,
                scan_cv=expansion.scanned_cv,
                scan_linkedin=expansion.scanned_linkedin,
                scan_diplomas=expansion.scanned_diplomas,
                scan_references=expansion.scanned_references,
                scan_github=expansion.scanned_github,
                scan_other_urls=expansion.scanned_other_urls,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            logger.error("Background expand failed for %s: %s", expansion_id, exc)


# ── Scanner functions ─────────────────────────────────────────────────────────


def _scan_cv_folder() -> list[dict[str, Any]]:
    """Scan documents/cv/ for CV PDFs and extract experience items."""
    items: list[dict[str, Any]] = []
    cv_dir = EXPAND_DOCS_DIR / "cv"
    if not cv_dir.exists():
        return items

    for pdf_path in cv_dir.glob("*.pdf"):
        text = _extract_text_from_pdf(pdf_path)
        if not text:
            continue

        lines = text.split("\n")
        title = lines[0].strip() if lines else pdf_path.stem

        items.append({
            "id": f"cv_{len(items)}",
            "source": "cv",
            "type": "job_bullet",
            "title": title,
            "description": text[:500],
            "date": "",
            "source_file": pdf_path.name,
        })

    return items


def _scan_linkedin_folder() -> list[dict[str, Any]]:
    """Scan documents/linkedin/ for LinkedIn export JSON files."""
    items: list[dict[str, Any]] = []
    li_dir = EXPAND_DOCS_DIR / "linkedin"
    if not li_dir.exists():
        return items

    for json_path in li_dir.glob("*.json"):
        try:
            with open(json_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        positions = data.get("positions", [])
        for pos in positions:
            items.append({
                "id": f"li_{len(items)}",
                "source": "linkedin",
                "type": "job_bullet",
                "title": pos.get("title", "Unknown"),
                "description": pos.get("description", ""),
                "date": pos.get("start_date", ""),
                "source_file": json_path.name,
            })

        certifications = data.get("certifications", [])
        for cert in certifications:
            items.append({
                "id": f"li_{len(items)}",
                "source": "linkedin",
                "type": "certification",
                "title": cert.get("name", "Unknown"),
                "description": cert.get("authority", ""),
                "date": cert.get("date", ""),
                "source_file": json_path.name,
            })

    return items


def _scan_diplomas_folder() -> list[dict[str, Any]]:
    """Scan documents/diplomas/ for diploma PDFs."""
    items: list[dict[str, Any]] = []
    diplomas_dir = EXPAND_DOCS_DIR / "diplomas"
    if not diplomas_dir.exists():
        return items

    for pdf_path in diplomas_dir.glob("*.pdf"):
        text = _extract_text_from_pdf(pdf_path)
        if not text:
            continue

        items.append({
            "id": f"dip_{len(items)}",
            "source": "diplomas",
            "type": "course",
            "title": pdf_path.stem,
            "description": text[:500],
            "date": "",
            "source_file": pdf_path.name,
        })

    return items


def _scan_references_folder() -> list[dict[str, Any]]:
    """Scan documents/references/ for reference letter PDFs."""
    items: list[dict[str, Any]] = []
    ref_dir = EXPAND_DOCS_DIR / "references"
    if not ref_dir.exists():
        return items

    for pdf_path in ref_dir.glob("*.pdf"):
        text = _extract_text_from_pdf(pdf_path)
        if not text:
            continue

        items.append({
            "id": f"ref_{len(items)}",
            "source": "references",
            "type": "volunteer",
            "title": pdf_path.stem,
            "description": text[:500],
            "date": "",
            "source_file": pdf_path.name,
        })

    return items


def fetch_github_repos(github_url: str) -> list[dict[str, Any]]:
    """Fetch public repositories from a GitHub profile URL.

    Uses a simple heuristic (no external API key needed):
    Extracts the username from the URL and builds repo info.
    """
    repos: list[dict[str, Any]] = []

    match = re.search(r"github\.com/([^/]+)", github_url)
    if not match:
        return repos

    username = match.group(1)
    api_url = f"https://api.github.com/users/{username}/repos?per_page=20&sort=updated"

    try:
        import httpx

        response = httpx.get(api_url, timeout=10)
        if response.status_code == 200:
            for repo in response.json():
                repos.append({
                    "name": repo.get("name", ""),
                    "description": repo.get("description") or "",
                    "language": repo.get("language") or "",
                    "topics": repo.get("topics", []),
                    "stars": repo.get("stargazers_count", 0),
                    "url": repo.get("html_url", ""),
                })
    except Exception as exc:
        logger.warning("Failed to fetch GitHub repos for %s: %s", github_url, exc)

    return repos


def _make_github_items(repos: list[dict[str, Any]], base_url: str) -> list[dict[str, Any]]:
    """Convert GitHub repo data to experience items."""
    items: list[dict[str, Any]] = []
    for repo in repos:
        items.append({
            "id": f"gh_{len(items)}",
            "source": "github",
            "type": "repo",
            "title": repo.get("name", "Unknown"),
            "description": repo.get("description", ""),
            "date": "",
            "source_file": base_url,
            "language": repo.get("language", ""),
            "topics": repo.get("topics", []),
            "stars": repo.get("stars", 0),
            "url": repo.get("url", ""),
        })
    return items


def _scan_other_urls(candidate: CandidateProfile) -> list[dict[str, Any]]:
    """Scan other URLs from the candidate profile (portfolio, Kaggle, etc.)."""
    items: list[dict[str, Any]] = []

    urls: list[str] = []
    if candidate.linkedin_url:
        urls.append(candidate.linkedin_url)
    if candidate.github_url:
        urls.append(candidate.github_url)

    for url in urls:
        items.append({
            "id": f"url_{len(items)}",
            "source": "other_url",
            "type": "project",
            "title": url,
            "description": "",
            "date": "",
            "source_file": url,
        })

    return items


async def _scan_other_urls_async(candidate: CandidateProfile) -> list[dict[str, Any]]:
    """Async version of _scan_other_urls (used for patching in tests)."""
    return _scan_other_urls(candidate)


def _extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from a PDF file using pypdf (pure Python)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except ImportError:
        logger.warning("pypdf not installed — cannot extract PDF text")
        return ""


# ── Prompt builders ───────────────────────────────────────────────────────────


def build_competency_enrichment_prompt(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build the LLM prompt for competency enrichment from experience items."""
    items_text = "\n".join(
        f"ID: {item['id']} | Source: {item['source']} | Title: {item['title']} | "
        f"Description: {item.get('description', 'N/A')}"
        for item in items
    )

    system_prompt = f"""You are a competency extraction assistant. Given a list of experience items (job bullets, certifications, projects, etc.), extract the implied competencies (skills, tools, methodologies) for each item.

Return your response as a JSON object with an "enrichments" array where each element has:
- experience_item_id: the ID of the item
- competencies: list of strings (implied skills/knowledge)
- source: "direct_lookup" or "inferred"
- source_urls: empty list

Experience items to analyze:
{items_text}

Be specific and technical. Extract 2-5 competencies per item."""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Extract the competencies from the above experience items."},
    ]


def build_proposed_additions_prompt(
    candidate: CandidateProfile,
    enriched: list[Any],
) -> list[dict[str, str]]:
    """Build the LLM prompt for proposing profile additions from enriched competencies."""
    profile_summary = f"Candidate: {candidate.full_name or 'Unknown'}"
    if candidate.skills:
        profile_summary += f"\nCurrent skills: {json.dumps(candidate.skills, ensure_ascii=False, indent=2)[:1000]}"

    enriched_text = "\n".join(
        f"- Item {e.experience_item_id}: {', '.join(e.competencies[:5])}"
        for e in enriched
    )

    system_prompt = f"""You are a career profile enhancement assistant. Given a candidate's current profile and a list of enriched competencies (discovered from documents, LinkedIn, GitHub, etc.), propose additions to the candidate's profile.

IMPORTANT GUARDRAIL:
- Only propose additions that are DIRECTLY supported by the discovered competencies.
- Never fabricate or hallucinate skills.
- Proposed additions must be additive — never suggest removing or modifying existing profile content.

{profile_summary}

Enriched competencies from experience items:
{enriched_text}

Return a JSON object with an "additions" array where each element has:
- category: one of "skills.programming_ml", "skills.software_tools", "skills.domain_expertise"
- item: a dict or string value to add
- reason: why this addition is supported by the evidence

Propose 2-5 high-confidence additions only."""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Propose profile additions based on the enriched competencies."},
    ]
