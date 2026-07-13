"""Expand service — discovers hidden competencies from documents and online presence.

Implements the /expand workflow from the original repo:
1. Scans all available sources for "experience items" (documents/cv/, documents/linkedin/,
   documents/diplomas/, documents/references/, GitHub profile, other URLs in profile)
2. For each experience item, searches the web to extract competencies (direct lookup + inference)
3. Proposes additions to the candidate profile (additive only — never modifies existing content)
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

from app.core.settings import get_settings
from app.db.models import (
    CandidateProfile,
    CompetencyExpansion,
    User,
)
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.llm.adapter import llm_completion_structured
from app.schemas.expand import (
    EnrichedCompetenciesLLMOutput,
    ExperienceItemLLMOutput,
    ProposedAdditionsLLMOutput,
)

settings = get_settings()

# ── Guardrail constant ──────────────────────────────────────────────

EXPAND_GUARDRAIL = """
IMPORTANT GUARDRAIL: You are discovering competencies from a candidate's documents and online presence.
You MUST NEVER invent, hallucinate, or assume experience, titles, companies, or skills
that the candidate does not explicitly have in their profile or documents.

Your role is to:
- Identify genuine experience items from the provided documents/URLs
- Extract competencies that are explicitly mentioned or can be reasonably inferred
- For web searches, only report competencies that are verifiably associated with the item
- Flag when something cannot be verified

If a competency cannot be verified from the source material, do not include it.
The candidate must be able to defend every proposed addition in an interview without backtracking.
"""

# ── Document scanning helpers ───────────────────────────────────────


def _get_documents_dir() -> Path:
    """Get the documents directory path."""
    return Path(settings.documents_dir) if hasattr(settings, "documents_dir") else Path("documents")


def _scan_cv_folder() -> list[dict[str, Any]]:
    """Scan documents/cv/ for experience items."""
    items = []
    cv_dir = _get_documents_dir() / "cv"
    if not cv_dir.exists():
        return items

    for file_path in cv_dir.glob("*"):
        if file_path.suffix.lower() in {".pdf", ".txt", ".md", ".docx"}:
            # In a real implementation, you'd extract text from the file
            # For now, we'll note the file exists
            items.append({
                "source": "cv",
                "type": "document",
                "title": file_path.stem,
                "description": f"CV document: {file_path.name}",
                "date": None,
                "source_file": str(file_path),
            })
    return items


def _scan_linkedin_folder() -> list[dict[str, Any]]:
    """Scan documents/linkedin/ for experience items."""
    items = []
    linkedin_dir = _get_documents_dir() / "linkedin"
    if not linkedin_dir.exists():
        return items

    for file_path in linkedin_dir.glob("*"):
        if file_path.suffix.lower() in {".pdf", ".txt", ".md", ".json"}:
            items.append({
                "source": "linkedin",
                "type": "document",
                "title": file_path.stem,
                "description": f"LinkedIn export: {file_path.name}",
                "date": None,
                "source_file": str(file_path),
            })
    return items


def _scan_diplomas_folder() -> list[dict[str, Any]]:
    """Scan documents/diplomas/ for experience items."""
    items = []
    diplomas_dir = _get_documents_dir() / "diplomas"
    if not diplomas_dir.exists():
        return items

    for file_path in diplomas_dir.glob("*"):
        if file_path.suffix.lower() in {".pdf", ".txt", ".md", ".jpg", ".png"}:
            items.append({
                "source": "diplomas",
                "type": "certification",
                "title": file_path.stem,
                "description": f"Diploma/certificate: {file_path.name}",
                "date": None,
                "source_file": str(file_path),
            })
    return items


def _scan_references_folder() -> list[dict[str, Any]]:
    """Scan documents/references/ for experience items."""
    items = []
    refs_dir = _get_documents_dir() / "references"
    if not refs_dir.exists():
        return items

    for file_path in refs_dir.glob("*"):
        if file_path.suffix.lower() in {".pdf", ".txt", ".md"}:
            items.append({
                "source": "references",
                "type": "reference",
                "title": file_path.stem,
                "description": f"Reference letter: {file_path.name}",
                "date": None,
                "source_file": str(file_path),
            })
    return items


def _scan_github_profile(candidate: CandidateProfile) -> list[dict[str, Any]]:
    """Scan GitHub profile for repositories as experience items."""
    items = []
    github_url = candidate.github_url
    if not github_url:
        return items

    # Extract username from URL
    match = re.search(r"github\.com/([^/?#]+)", github_url)
    if not match:
        return items

    username = match.group(1)
    # In a real implementation, you'd call GitHub API here
    # For now, we note the profile exists
    items.append({
        "source": "github",
        "type": "profile",
        "title": f"GitHub: {username}",
        "description": f"GitHub profile with repositories",
        "date": None,
        "source_file": github_url,
    })
    return items


def _scan_other_urls(candidate: CandidateProfile) -> list[dict[str, Any]]:
    """Scan other URLs from candidate profile (portfolio, Kaggle, etc.)."""
    items = []
    urls = []

    if candidate.linkedin_url:
        urls.append(("linkedin", candidate.linkedin_url))
    if candidate.github_url:
        urls.append(("github", candidate.github_url))
    # Add other URLs from profile if present

    for source, url in urls:
        items.append({
            "source": "other_url",
            "type": "profile",
            "title": f"{source.capitalize()} profile",
            "description": f"Professional profile: {url}",
            "date": None,
            "source_file": url,
        })

    return items


# ── Prompt builders ─────────────────────────────────────────────────


def build_experience_extraction_prompt(
    source: str,
    content: str,
) -> list[dict[str, str]]:
    """Build prompt for extracting experience items from document content."""
    system_prompt = f"""{EXPAND_GUARDRAIL}

You are extracting "experience items" from a {source} document.
An experience item is anything that implies skills, knowledge, or competencies:
- Courses, certifications, degrees
- Job responsibilities and achievements
- Projects (personal, academic, professional)
- Volunteer work, extracurriculars
- Publications, presentations
- Technical projects, repositories

For each item, extract:
- type: course, certification, job_bullet, project, volunteer, repo, publication, other
- title: concise name
- description: what was done, technologies used, outcomes
- date: when (if mentioned)
- source_file: the document this came from

Return ONLY valid JSON matching the ExperienceItemLLMOutput schema.
"""

    user_prompt = f"""Extract experience items from this {source} document:

{content[:8000]}  # Truncate if too long

Return JSON with "items" array.
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_competency_enrichment_prompt(
    experience_items: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build prompt for enriching experience items with competencies via web search."""
    items_text = "\n\n".join(
        f"Item {i+1} (ID: {item.get('id', i)}):\n"
        f"  Title: {item.get('title', '')}\n"
        f"  Description: {item.get('description', '')}\n"
        f"  Type: {item.get('type', '')}"
        for i, item in enumerate(experience_items)
    )

    system_prompt = f"""{EXPAND_GUARDRAIL}

You are enriching experience items with implied competencies.
For each item, determine competencies through TWO approaches:

1. DIRECT LOOKUP: If the item names a specific course, certification, tool, framework,
   or method, search for its official syllabus/skills list.
   Example: "AWS Solutions Architect Associate" → search "AWS Solutions Architect Associate skills covered"

2. INFERRED COMPETENCIES: From the description, infer what skills/knowledge are required.
   Example: "Built ML pipeline with PyTorch and Kubernetes" → Python, PyTorch, Kubernetes, MLOps, Docker

For each item, return:
- experience_item_id: the item's ID
- competencies: list of specific skills/technologies/methods
- source: "direct_lookup" or "inferred"
- source_urls: URLs of official sources (for direct lookups)

Return ONLY valid JSON matching the EnrichedCompetenciesLLMOutput schema.
"""

    user_prompt = f"""Enrich these experience items with competencies:

{items_text}

Return JSON with "enrichments" array.
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_proposed_additions_prompt(
    candidate: CandidateProfile,
    enriched_competencies: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build prompt for proposing additions to the candidate profile."""
    # Build current profile summary
    current_skills = candidate.skills or {}
    current_programming = [s.get("language", "") for s in current_skills.get("programming_ml", [])]
    current_domain = current_skills.get("domain_expertise", [])
    current_tools = current_skills.get("software_tools", [])

    # Collect all enriched competencies
    all_competencies = []
    for enrichment in enriched_competencies:
        all_competencies.extend(enrichment.get("competencies", []))

    # Deduplicate
    unique_competencies = list(set(all_competencies))

    system_prompt = f"""{EXPAND_GUARDRAIL}

You are proposing additions to a candidate's profile based on discovered competencies.

CURRENT PROFILE SKILLS:
- Programming/ML: {', '.join(current_programming) if current_programming else 'None'}
- Domain Expertise: {', '.join(current_domain) if current_domain else 'None'}
- Software/Tools: {', '.join(current_tools) if current_tools else 'None'}

DISCOVERED COMPETENCIES (from document/web analysis):
{', '.join(unique_competencies) if unique_competencies else 'None'}

TASK:
Propose additions to the candidate's profile. For each competency:
1. Check if it's already in the profile (exact or close match) — if so, SKIP
2. Categorize: programming_ml, domain_expertise, or software_tools
3. Assign proficiency: Expert, Advanced, Intermediate, Basic (based on evidence)
3. Provide evidence: which experience item(s) support this
4. Cite source: which document/web search

Only propose competencies with genuine evidence. Never invent skills.
Return ONLY valid JSON matching the ProposedAdditionsLLMOutput schema.
"""

    user_prompt = "Propose profile additions based on discovered competencies. Return JSON with 'additions' array."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ── Main orchestration ──────────────────────────────────────────────


async def execute_expand(
    db: AsyncSession,
    user_id: str,
    scan_cv: bool = True,
    scan_linkedin: bool = True,
    scan_diplomas: bool = True,
    scan_references: bool = True,
    scan_github: bool = True,
    scan_other_urls: bool = True,
) -> CompetencyExpansion:
    """Execute a full competency expansion run.

    Args:
        db: Database session
        user_id: Authenticated user ID
        scan_*: Which sources to scan

    Returns:
        The created CompetencyExpansion record
    """
    # 1. Get candidate profile
    candidate_result = await db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user_id)
    )
    candidate = candidate_result.scalar_one_or_none()
    if candidate is None:
        raise ProfileIncompleteError("Candidate profile not found. Run /setup first.")

    # 2. Create expansion record
    expansion = CompetencyExpansion(
        user_id=user_id,
        candidate_id=candidate.id,
        scanned_cv=scan_cv,
        scanned_linkedin=scan_linkedin,
        scanned_diplomas=scan_diplomas,
        scanned_references=scan_references,
        scanned_github=scan_github,
        scanned_other_urls=scan_other_urls,
        status="running",
    )
    db.add(expansion)
    await db.flush()

    try:
        # 3. Scan all sources for experience items
        all_items = []

        if scan_cv:
            cv_items = _scan_cv_folder()
            for item in cv_items:
                item["id"] = f"cv_{len(all_items)}"
            all_items.extend(cv_items)

        if scan_linkedin:
            li_items = _scan_linkedin_folder()
            for item in li_items:
                item["id"] = f"li_{len(all_items)}"
            all_items.extend(li_items)

        if scan_diplomas:
            dip_items = _scan_diplomas_folder()
            for item in dip_items:
                item["id"] = f"dip_{len(all_items)}"
            all_items.extend(dip_items)

        if scan_references:
            ref_items = _scan_references_folder()
            for item in ref_items:
                item["id"] = f"ref_{len(all_items)}"
            all_items.extend(ref_items)

        if scan_github:
            gh_items = _scan_github_profile(candidate)
            for item in gh_items:
                item["id"] = f"gh_{len(all_items)}"
            all_items.extend(gh_items)

        if scan_other_urls:
            url_items = _scan_other_urls(candidate)
            for item in url_items:
                item["id"] = f"url_{len(all_items)}"
            all_items.extend(url_items)

        # Store experience items
        expansion.experience_items = all_items
        await db.flush()

        # 4. Enrich competencies via LLM (if items found)
        enriched = []
        if all_items:
            messages = build_competency_enrichment_prompt(all_items)
            try:
                result: EnrichedCompetenciesLLMOutput = await llm_completion_structured(
                    messages=messages,
                    output_schema=EnrichedCompetenciesLLMOutput,
                    provider=settings.llm_default_provider,
                    temperature=0.3,
                    max_tokens=3000,
                )
                enriched = result.enrichments
            except Exception as e:
                raise LLMError(f"Competency enrichment failed: {e}") from e

        # 5. Propose additions to profile
        proposed = []
        if enriched:
            # Get candidate for profile context
            candidate_result = await db.execute(
                select(CandidateProfile).where(CandidateProfile.user_id == expansion.user_id)
            )
            candidate = candidate_result.scalar_one()

            messages = build_proposed_additions_prompt(candidate, enriched)
            try:
                result: ProposedAdditionsLLMOutput = await llm_completion_structured(
                    messages=messages,
                    output_schema=ProposedAdditionsLLMOutput,
                    provider=settings.llm_default_provider,
                    temperature=0.3,
                    max_tokens=2000,
                )
                proposed = result.additions
            except Exception as e:
                raise LLMError(f"Proposed additions failed: {e}") from e

        # 6. Update expansion record
        expansion.experience_items = all_items
        expansion.enriched_competencies = enriched
        expansion.proposed_additions = proposed
        expansion.status = "completed"
        await db.commit()

    except Exception as e:
        expansion.status = "failed"
        expansion.error_message = str(e)
        await db.commit()
        raise

    await db.refresh(expansion)
    return expansion


# ── Query helpers ───────────────────────────────────────────────────


async def get_expansion(
    db: AsyncSession, expansion_id: str, user_id: str
) -> CompetencyExpansion:
    """Get a competency expansion by ID, verifying ownership."""
    result = await db.execute(
        select(CompetencyExpansion)
        .where(CompetencyExpansion.id == expansion_id)
        .where(CompetencyExpansion.user_id == user_id)
    )
    expansion = result.scalar_one_or_none()
    if expansion is None:
        raise NotFoundError("Competency expansion not found.")
    return expansion


async def list_expansions(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[CompetencyExpansion]:
    """List competency expansions for a user."""
    result = await db.execute(
        select(CompetencyExpansion)
        .where(CompetencyExpansion.user_id == user_id)
        .order_by(CompetencyExpansion.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())