"""Upskill service — identifies skill gaps and generates a learning plan.

Implements the /upskill workflow from the original repo:
- Pass 1: Hard skill diff (frequency map + fit weighting)
- Pass 2: LLM synthesis (domain, soft, tooling, credential gaps)
- Pass 3: Gap heatmap + learning plan with web-searched resources
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import (
    CandidateProfile,
    JobPosting,
    RankEvaluation,
    Upskill,
)
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.llm.adapter import llm_completion_structured
from app.schemas.upskill import (
    GapHeatmapLLMOutput,
    HardSkillGapsLLMOutput,
    LearningPlanLLMOutput,
    SynthesizedGapsLLMOutput,
    UpskillRequest,
)

settings = get_settings()

# ── Guardrail constant ──────────────────────────────────────────────

UPSKILL_GUARDRAIL = """
IMPORTANT GUARDRAIL: You are analyzing a candidate's skill gaps against job postings.
You MUST NEVER invent, hallucinate, or assume skills that the candidate does not have.
Your role is to:
- Identify genuine gaps between the candidate's actual skills and job requirements
- Suggest learning resources that are verifiable and reputable
- Be honest about what the candidate lacks vs. what they have

If a skill cannot be verified from the source material, do not include it.
The candidate must be able to defend every proposed learning item in an interview.
"""

# ── Skill extraction helpers ────────────────────────────────────────


def _extract_skills_from_profile(candidate: CandidateProfile) -> set[str]:
    """Extract all skills from candidate profile as a normalized set."""
    skills = set()
    if not candidate.skills:
        return skills

    # Programming/ML skills
    for prog in candidate.skills.get("programming_ml", []):
        lang = prog.get("language", "").strip().lower()
        if lang:
            skills.add(lang)
        for fw in prog.get("frameworks", []):
            skills.add(fw.strip().lower())

    # Domain expertise
    for domain in candidate.skills.get("domain_expertise", []):
        skills.add(domain.strip().lower())

    # Software tools
    for tool in candidate.skills.get("software_tools", []):
        skills.add(tool.strip().lower())

    return skills


def _extract_requirements_from_job(job: JobPosting) -> list[str]:
    """Extract required skills from a job posting."""
    requirements = []
    if job.requirements:
        for req in job.requirements:
            # Simple extraction: split by common delimiters
            parts = re.split(r"[,;•\n]", req)
            for part in parts:
                part = part.strip().lower()
                if part and len(part) > 2:
                    requirements.append(part)
    return requirements


def _normalize_skill(skill: str) -> str:
    """Normalize a skill string for comparison."""
    return re.sub(r"[^a-z0-9+#.]", "", skill.lower().strip())


# ── Prompt builders ─────────────────────────────────────────────────


def build_pass1_prompt(
    candidate_skills: set[str],
    job_requirements: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build prompt for Pass 1: Hard skill diff with frequency + fit weighting."""
    candidate_skills_text = ", ".join(sorted(candidate_skills)) if candidate_skills else "None"

    jobs_text = ""
    for i, job in enumerate(job_requirements):
        jobs_text += f"\nJob {i+1} ({job['company']} - {job['title']}):\n"
        jobs_text += f"  Requirements: {', '.join(job['requirements'])}\n"
        jobs_text += f"  Fit rating: {job.get('fit_rating', 'N/A')}/100\n"

    system_prompt = f"""{UPSKILL_GUARDRAIL}

You are an expert technical recruiter analyzing skill gaps between a candidate and job postings.

CANDIDATE SKILLS:
{candidate_skills_text}

JOB POSTINGS ANALYZED:
{jobs_text}

TASK — Pass 1: Hard Skill Diff
For each job, extract explicitly mentioned hard technical skills (languages, frameworks, tools, platforms, certifications).
Build a frequency map: how many jobs mention each skill.
Apply fit weighting: skills from lower-fit jobs (lower rank_score) contribute MORE to the gap score.
Formula: gap_score = sum(frequency * (100 - fit_rating) / 100) for each job mentioning the skill.

Remove any skill the candidate already has (exact or close match).
Return ONLY skills the candidate LACKS.

Return JSON with "gaps" array, each item:
- skill: the missing skill name
- type: "hard"
- priority: "Critical" (score >= 3), "High" (score >= 2), "Medium" (score >= 1), "Low" (score > 0)
- source_jobs: list of job IDs mentioning this skill
- frequency: number of jobs mentioning it
- fit_weight: the calculated gap score (float)
"""

    user_prompt = "Analyze the hard skill gaps. Return only the JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_pass2_prompt(
    candidate_skills: set[str],
    hard_gaps: list[dict[str, Any]],
    job_context: str,
) -> list[dict[str, str]]:
    """Build prompt for Pass 2: LLM synthesis of domain/soft/tooling/credential gaps."""
    candidate_skills_text = ", ".join(sorted(candidate_skills)) if candidate_skills else "None"
    hard_gaps_text = "\n".join(f"- {g['skill']} (freq: {g['frequency']}, weight: {g['fit_weight']:.1f})" for g in hard_gaps)

    system_prompt = f"""{UPSKILL_GUARDRAIL}

You are an expert career coach analyzing skill gaps holistically.

CANDIDATE SKILLS:
{candidate_skills_text}

HARD SKILL GAPS (from Pass 1):
{hard_gaps_text}

JOB CONTEXT:
{job_context}

TASK — Pass 2: LLM Synthesis
Reason about gaps that Pass 1 would miss:
1. DOMAIN KNOWLEDGE: Industry-specific knowledge implied by job postings but not explicit skills
2. SOFT SKILLS: Communication, leadership, collaboration patterns mentioned in requirements
3. TOOLING/PROCESS: CI/CD, MLOps, testing, monitoring, agile methodologies
4. CREDENTIALS: Certifications, degrees, formal qualifications mentioned or implied

For each synthesized gap, provide:
- skill: the gap name
- type: "domain" | "soft" | "tooling" | "credential"
- priority: "Critical" | "High" | "Medium" | "Low"
- source: "LLM synthesis"
- evidence: brief justification from job postings

Return JSON with "gaps" array.
"""

    user_prompt = "Synthesize the non-hard-skill gaps. Return only the JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_heatmap_prompt(
    hard_gaps: list[dict[str, Any]],
    synthesized_gaps: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build prompt for combining Pass 1 + Pass 2 into unified heatmap."""
    hard_text = "\n".join(f"- {g['skill']} (hard, freq: {g['frequency']}, weight: {g['fit_weight']:.1f})" for g in hard_gaps)
    synth_text = "\n".join(f"- {g['skill']} ({g['type']}, priority: {g['priority']})" for g in synthesized_gaps)

    system_prompt = f"""{UPSKILL_GUARDRAIL}

You are combining two gap analyses into a single prioritized heatmap.

HARD SKILL GAPS (Pass 1):
{hard_text}

SYNTHESIZED GAPS (Pass 2):
{synth_text}

TASK:
Create a unified heatmap. For each unique skill, combine info from both passes.
Assign priority: Critical (weight >= 3 or Critical synthesis), High (weight >= 2 or High synthesis), Medium (weight >= 1 or Medium synthesis), Low (else).
Include gap_source describing where it came from.

Return JSON with "heatmap" array.
"""

    user_prompt = "Create the unified gap heatmap. Return only the JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_learning_plan_prompt(
    candidate: CandidateProfile,
    heatmap: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build prompt for generating a learning plan with web-searched resources."""
    candidate_summary = f"""
Name: {candidate.full_name or 'N/A'}
Current skills: {', '.join(sorted(_extract_skills_from_profile(candidate))) if candidate.skills else 'None'}
Experience: {len(candidate.experience or [])} roles
Education: {len(candidate.education or [])} degrees
"""

    heatmap_text = "\n".join(
        f"- {g['skill']} ({g['type']}, {g['priority']}): {g['gap_source']}"
        for g in heatmap
    )

    system_prompt = f"""{UPSKILL_GUARDRAIL}

You are creating a personalized learning plan for a candidate.

CANDIDATE PROFILE:
{candidate_summary}

GAP HEATMAP:
{heatmap_text}

TASK:
For each gap, find 2-3 high-quality learning resources (courses, videos, articles, certifications).
Prioritize: free/affordable, reputable sources (Coursera, edX, official docs, reputable YouTube, etc.).
Include: title, URL, format (course/video/article/certification), estimated hours, cost (free/paid), quality score (1-10).
Order the plan by priority (Critical first) and prerequisites.
Estimate total weeks per skill.

Return JSON with "plan" array.
"""

    user_prompt = "Generate the learning plan. Return only the JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ── Main orchestration ──────────────────────────────────────────────


async def execute_upskill(
    db: AsyncSession,
    user_id: str,
    mode: str = "aggregate",
    target_job_url: str | None = None,
    target_job_posting_id: str | None = None,
) -> Upskill:
    """Execute a full upskill analysis run.

    Args:
        db: Database session
        user_id: Authenticated user ID
        mode: "aggregate" (all tracked jobs) or "targeted" (single job)
        target_job_url: Job URL for targeted mode
        target_job_posting_id: Job posting ID for targeted mode

    Returns:
        The created Upskill record with full analysis
    """
    # 1. Get candidate profile
    candidate_result = await db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user_id)
    )
    candidate = candidate_result.scalar_one_or_none()
    if candidate is None:
        raise ProfileIncompleteError("Candidate profile not found. Run /setup first.")

    # 2. Get candidate skills
    candidate_skills = _extract_skills_from_profile(candidate)

    # 3. Select jobs to analyze
    if mode == "targeted" and target_job_posting_id:
        job_result = await db.execute(
            select(JobPosting).where(
                JobPosting.id == target_job_posting_id,
                JobPosting.user_id == user_id,
            )
        )
        jobs = [job_result.scalar_one_or_none()]
        if not jobs[0]:
            raise NotFoundError("Target job posting not found.")
    else:
        # Aggregate mode: all ranked jobs for this user
        job_result = await db.execute(
            select(JobPosting)
            .where(JobPosting.user_id == user_id)
            .where(JobPosting.status == "ranked")
            .order_by(JobPosting.rank_score.desc().nullslast())
        )
        jobs = list(job_result.scalars().all())

    if not jobs:
        raise NotFoundError("No ranked jobs found. Run /scrape and /rank first.")

    # 4. Create upskill record
    upskill = Upskill(
        user_id=user_id,
        candidate_id=candidate.id,
        target_job_posting_id=target_job_posting_id,
        target_job_url=target_job_url,
        status="running",
    )
    db.add(upskill)
    await db.flush()

    try:
        # 5. Pass 1: Hard skill diff
        job_requirements = []
        for job in jobs:
            eval_result = await db.execute(
                select(RankEvaluation).where(RankEvaluation.job_posting_id == job.id)
            )
            evaluation = eval_result.scalar_one_or_none()
            fit_rating = evaluation.overall_score if evaluation else 50

            job_requirements.append({
                "id": job.id,
                "company": job.company,
                "title": job.title,
                "requirements": _extract_requirements_from_job(job),
                "fit_rating": fit_rating,
            })

        hard_gaps = await _run_pass1(db, candidate_skills, job_requirements)
        upskill.hard_skill_gaps = hard_gaps
        await db.flush()

        # 6. Pass 2: LLM synthesis
        job_context = "\n".join(f"- {j['company']} - {j['title']}: {', '.join(j['requirements'][:5])}" for j in job_requirements)
        synthesized_gaps = await _run_pass2(db, candidate_skills, hard_gaps, job_context)
        upskill.synthesized_gaps = synthesized_gaps
        await db.flush()

        # 7. Pass 3: Heatmap
        heatmap = await _run_heatmap(db, hard_gaps, synthesized_gaps)
        upskill.gap_heatmap = heatmap
        await db.flush()

        # 8. Pass 4: Learning plan
        learning_plan = await _run_learning_plan(db, candidate, heatmap)
        upskill.learning_plan = learning_plan
        upskill.status = "completed"
        await db.commit()
        await db.refresh(upskill)

    except Exception as e:
        upskill.status = "failed"
        upskill.error_message = str(e)
        await db.commit()
        raise

    return upskill


async def _run_pass1(
    db: AsyncSession,
    candidate_skills: set[str],
    job_requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run Pass 1: Hard skill diff with frequency + fit weighting."""
    messages = build_pass1_prompt(candidate_skills, job_requirements)

    try:
        result: HardSkillGapsLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=HardSkillGapsLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=3000,
        )
        return [gap.model_dump() for gap in result.gaps]
    except Exception as e:
        raise LLMError(f"Pass 1 (hard skill diff) failed: {e}") from e


async def _run_pass2(
    db: AsyncSession,
    candidate_skills: set[str],
    hard_gaps: list[dict[str, Any]],
    job_context: str,
) -> list[dict[str, Any]]:
    """Run Pass 2: LLM synthesis of domain/soft/tooling/credential gaps."""
    messages = build_pass2_prompt(candidate_skills, hard_gaps, job_context)

    try:
        result: SynthesizedGapsLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=SynthesizedGapsLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.4,
            max_tokens=3000,
        )
        return [gap.model_dump() for gap in result.gaps]
    except Exception as e:
        raise LLMError(f"Pass 2 (LLM synthesis) failed: {e}") from e


async def _run_heatmap(
    db: AsyncSession,
    hard_gaps: list[dict[str, Any]],
    synthesized_gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine Pass 1 + Pass 2 into unified gap heatmap."""
    messages = build_heatmap_prompt(hard_gaps, synthesized_gaps)

    try:
        result: GapHeatmapLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=GapHeatmapLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=2000,
        )
        return [h.model_dump() for h in result.heatmap]
    except Exception as e:
        raise LLMError(f"Heatmap generation failed: {e}") from e


async def _run_learning_plan(
    db: AsyncSession,
    candidate: CandidateProfile,
    heatmap: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate learning plan with web-searched resources."""
    messages = build_learning_plan_prompt(candidate, heatmap)

    try:
        result: LearningPlanLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=LearningPlanLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.4,
            max_tokens=4000,
        )
        return [item.model_dump() for item in result.plan]
    except Exception as e:
        raise LLMError(f"Learning plan generation failed: {e}") from e


# ── Query helpers ───────────────────────────────────────────────────


async def get_upskill(
    db: AsyncSession, upskill_id: str, user_id: str
) -> Upskill:
    """Get an upskill by ID, verifying ownership."""
    result = await db.execute(
        select(Upskill)
        .where(Upskill.id == upskill_id)
        .where(Upskill.user_id == user_id)
    )
    upskill = result.scalar_one_or_none()
    if upskill is None:
        raise NotFoundError("Upskill analysis not found.")
    return upskill


async def list_upskills(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[Upskill]:
    """List upskill analyses for a user."""
    result = await db.execute(
        select(Upskill)
        .where(Upskill.user_id == user_id)
        .order_by(Upskill.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())