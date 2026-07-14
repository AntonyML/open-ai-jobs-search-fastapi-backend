"""Rank service — evaluates job postings against the candidate profile.

Implements the triage scoring from the original /rank command:
- Fetches unranked jobs (or re-ranks if requested)
- For each job, builds a prompt with candidate profile + job posting
- Calls LLM via LiteLLM for structured evaluation
- Parses, validates, and stores the evaluation
- Returns a ranked shortlist
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import CandidateProfile, JobPosting, RankEvaluation, User
from app.exceptions import NotFoundError, ProfileIncompleteError, LLMError
from app.llm.adapter import llm_completion_structured
from app.schemas.rank import RankLLMOutput, RankResult, RankedJobOut
from app.schemas.scrape import JobPostingSummary
from app.services.provider_credentials import get_user_active_provider_config

settings = get_settings()
logger = logging.getLogger(__name__)

# ── Guardrail constant (never user-configurable) ────────────────────

GUARDRAIL_SYSTEM_PROMPT = """
IMPORTANT GUARDRAIL: You are evaluating a candidate's fit for a job posting.
You MUST NEVER invent, hallucinate, or assume experience, titles, companies,
or skills that the candidate does not explicitly have in their profile.

Your role is to:
- Identify genuine matches between the candidate's actual experience and the job requirements
- Point out gaps where the job requires something the candidate doesn't have
- Suggest how the candidate could better FRAME their real experience
- Flag red flags that a recruiter would notice immediately

If the candidate lacks a required skill, say so honestly. Do not "fill in the blanks."
The candidate must be able to defend every claim in an interview without backtracking.
"""

# ── Prompt templates ────────────────────────────────────────────────


def build_rank_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
) -> list[dict[str, str]]:
    """Build the messages for the LLM rank evaluation."""

    # Build candidate summary
    candidate_summary = _build_candidate_summary(candidate)

    # Build job summary
    job_summary = _build_job_summary(job)

    system_prompt = f"""{GUARDRAIL_SYSTEM_PROMPT}

You are an expert technical recruiter evaluating a candidate's fit for a specific role.
Score each dimension 0-100 and provide structured insights.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

EVALUATION FRAMEWORK (from the candidate's personalized evaluation criteria):

Technical Skills Match (30% weight):
- Strong match areas: {candidate.skills.get('programming_ml', []) if candidate.skills else 'Not specified'}
- Moderate match areas: {candidate.skills.get('domain_expertise', []) if candidate.skills else 'Not specified'}
- Weak match areas: {candidate.skills.get('software_tools', []) if candidate.skills else 'Not specified'}

Experience Match (25% weight):
- Direct experience domains: {candidate.experience[0].get('company') if candidate.experience else 'Not specified'}
- Adjacent experience: {candidate.projects if candidate.projects else 'Not specified'}

Behavioral/Culture Fit (15% weight):
- Profile type: {candidate.profile_statement if candidate.profile_statement else 'Not specified'}

Career Alignment (30% weight):
- Career goals: {candidate.profile_statement if candidate.profile_statement else 'Not specified'}

Location constraint: {candidate.location if candidate.location else 'Not specified'}

Return ONLY valid JSON matching the RankLLMOutput schema.
"""

    user_prompt = f"""Evaluate this candidate for the job posting. Be honest about gaps — do not invent experience.

Return JSON with:
- technical_score (0-100)
- experience_score (0-100)
- behavioral_score (0-100)
- career_score (0-100)
- location_status: "PASS" | "FAIL" | "FLAG"
- deadline (YYYY-MM-DD or null)
- deadline_urgent (boolean)
- strengths (max 3 items)
- gaps (max 3 items)
- missing_keywords (max 5 items) — terms from the job posting absent from the candidate's profile
- red_flags (max 3 items) — things a recruiter would notice negatively in first 10 seconds
- language (en/da/...)"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _build_candidate_summary(candidate: CandidateProfile) -> str:
    """Build a concise text summary of the candidate profile."""
    parts = []

    if candidate.full_name:
        parts.append(f"Name: {candidate.full_name}")
    if candidate.location:
        parts.append(f"Location: {candidate.location}")
    if candidate.profile_statement:
        parts.append(f"Profile: {candidate.profile_statement}")

    if candidate.education:
        edu_lines = []
        for e in candidate.education[:3]:
            line = f"  - {e.get('degree', '')} at {e.get('institution', '')}"
            if e.get("period"):
                line += f" ({e['period']})"
            edu_lines.append(line)
        parts.append("Education:\n" + "\n".join(edu_lines))

    if candidate.experience:
        exp_lines = []
        for e in candidate.experience[:3]:
            line = f"  - {e.get('title', '')} at {e.get('company', '')}"
            if e.get("start_date") or e.get("end_date"):
                line += f" ({e.get('start_date', '')}–{e.get('end_date', '')})"
            if e.get("bullets"):
                for b in e["bullets"][:2]:
                    line += f"\n    • {b}"
            exp_lines.append(line)
        parts.append("Experience:\n" + "\n".join(exp_lines))

    if candidate.skills:
        skills = candidate.skills
        if skills.get("programming_ml"):
            parts.append("Technical Skills: " + ", ".join(
                f"{s.get('language', '')} ({s.get('proficiency', '')})"
                for s in skills["programming_ml"][:5]
            ))
        if skills.get("domain_expertise"):
            parts.append("Domain Expertise: " + ", ".join(skills["domain_expertise"][:5]))
        if skills.get("software_tools"):
            parts.append("Tools: " + ", ".join(skills["software_tools"][:5]))

    return "\n\n".join(parts)


def _build_job_summary(job: JobPosting) -> str:
    """Build a concise text summary of the job posting."""
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company or 'Not specified'}",
        f"Location: {job.location or 'Not specified'}",
    ]

    if job.posting_date:
        parts.append(f"Posted: {job.posting_date}")
    if job.deadline:
        parts.append(f"Deadline: {job.deadline}")
    if job.employment_type:
        parts.append(f"Type: {job.employment_type}")

    if job.description:
        # Truncate long descriptions
        desc = job.description[:2000] + ("..." if len(job.description) > 2000 else "")
        parts.append(f"Description:\n{desc}")

    if job.requirements:
        reqs = "\n".join(f"  • {r}" for r in job.requirements[:10])
        parts.append(f"Requirements:\n{reqs}")

    return "\n\n".join(parts)


# ── Scoring helpers ─────────────────────────────────────────────────


def compute_overall_score(
    technical: int,
    experience: int,
    behavioral: int,
    career: int,
) -> int:
    """Compute weighted overall score per the evaluation framework.

    Weights: Technical 30%, Experience 25%, Behavioral 15%, Career 30%
    """
    return round(
        technical * 0.30
        + experience * 0.25
        + behavioral * 0.15
        + career * 0.30
    )


def score_to_verdict(score: int) -> str:
    """Map overall score to verdict band."""
    if score >= 75:
        return "Strong Fit"
    if score >= 60:
        return "Good Fit"
    if score >= 45:
        return "Moderate Fit"
    if score >= 30:
        return "Weak Fit"
    return "Poor Fit"


# ── Main orchestration ──────────────────────────────────────────────


async def execute_rank(
    db: AsyncSession,
    user_id: str,
    focus_area: str | None = None,
    re_rank: bool = False,
    top_n: int = 5,
) -> RankResult:
    """Execute a rank run for the user's job postings.

    Args:
        db: Database session.
        user_id: The authenticated user's ID.
        focus_area: Optional filter for job title/description.
        re_rank: If True, re-evaluate already-ranked jobs.
        top_n: Size of the shortlist to return.

    Returns:
        RankResult with shortlist and counts.
    """
    # 1. Get candidate profile
    candidate = await _get_candidate_profile(db, user_id)
    provider_config = await get_user_active_provider_config(db, user_id)

    # 2. Select jobs to rank
    jobs = await _select_jobs_to_rank(db, user_id, focus_area, re_rank)

    if not jobs:
        return RankResult(
            ranked_count=0,
            shortlist=[],
            below_threshold=0,
            expired_or_vetoed=0,
            message="No new jobs to rank.",
        )

    # 3. Rank each job
    ranked_jobs = []
    failed_jobs = 0
    below_threshold = 0
    expired_or_vetoed = 0

    for index, job in enumerate(jobs, start=1):
        logger.info("Evaluating job %d/%d: %s", index, len(jobs), job.id)
        try:
            evaluation = await _rank_single_job(db, candidate, job, provider_config)
            ranked_jobs.append((job, evaluation))
            logger.info("Finished job %d/%d: %s", index, len(jobs), job.id)
        except LLMError as exc:
            failed_jobs += 1
            logger.exception("LLM ranking failed for job %s: %s", job.id, exc)
            continue

    # 4. Sort by overall score (desc), deadline urgency as tiebreaker
    ranked_jobs.sort(
        key=lambda x: (
            x[1].overall_score,
            x[1].deadline_urgent,
        ),
        reverse=True,
    )

    # 5. Build shortlist and counts
    shortlist = []
    for job, eval_ in ranked_jobs:
        if eval_.location_status == "FAIL":
            expired_or_vetoed += 1
        elif len(shortlist) < top_n:
            shortlist.append(
                RankedJobOut(
                    job=JobPostingSummary.model_validate(job),
                    evaluation=eval_,
                )
            )
        elif eval_.overall_score < 30:
            below_threshold += 1
        else:
            expired_or_vetoed += 1

    # 6. Update job statuses
    for job, eval_ in ranked_jobs:
        job.status = "ranked"
        job.rank_score = eval_.overall_score
        job.rank_verdict = eval_.verdict
        job.rank_date = datetime.now(timezone.utc)

    await db.commit()
    logger.info("Successful evaluations: %d; failed evaluations: %d", len(ranked_jobs), failed_jobs)

    return RankResult(
        ranked_count=len(ranked_jobs),
        shortlist=shortlist,
        below_threshold=below_threshold,
        expired_or_vetoed=expired_or_vetoed,
        message=f"Ranked {len(ranked_jobs)} jobs. Top {len(shortlist)} in shortlist. {failed_jobs} failed.",
    )


async def _get_candidate_profile(db: AsyncSession, user_id: str) -> CandidateProfile:
    """Get the candidate profile, raising if incomplete."""
    result = await db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise ProfileIncompleteError(
            "Candidate profile not found. Run /setup/profile first."
        )
    # Check for minimum required fields
    if not profile.full_name or not profile.experience:
        raise ProfileIncompleteError(
            "Profile is incomplete. Please fill in at least name and experience."
        )
    return profile


async def _select_jobs_to_rank(
    db: AsyncSession,
    user_id: str,
    focus_area: str | None,
    re_rank: bool,
) -> list[JobPosting]:
    """Select jobs that need ranking."""
    query = select(JobPosting).where(JobPosting.user_id == user_id)

    if not re_rank:
        # A posting is un-evaluated when it has no persisted rank score.  Use
        # this as the source of truth instead of relying only on the lifecycle
        # status, because the scrape view can expose persisted postings whose
        # status was changed independently of the ranking workflow.
        query = query.where(
            or_(
                JobPosting.status == "new",
                JobPosting.rank_score.is_(None),
            )
        )

    # Focus area is guidance for ranking, not a literal SQL pre-filter.  Job
    # titles use many valid variants (for example AI Engineer vs AI
    # Engineering), so filtering here would discard relevant postings before
    # the LLM can evaluate them.
    if focus_area:
        logger.info("Focus area passed to ranking guidance: %s", focus_area)

    query = query.order_by(JobPosting.created_at.desc())
    logger.info("Rank SQL: %s", query)
    result = await db.execute(query)
    jobs = list(result.scalars().all())
    logger.info("Rank jobs selected: %d", len(jobs))
    return jobs


async def _rank_single_job(
    db: AsyncSession,
    candidate: CandidateProfile,
    job: JobPosting,
    provider_config: dict[str, Any],
) -> RankEvaluation:
    """Rank a single job posting against the candidate profile."""
    messages = build_rank_prompt(candidate, job)

    # Call LLM with structured output
    try:
        llm_output: RankLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=RankLLMOutput,
            provider=provider_config["provider"],
            model=provider_config["model"],
            api_key=provider_config.get("api_key"),
            api_base=provider_config.get("api_base"),
            temperature=0.3,
            max_tokens=2048,
        )
    except Exception as exc:
        raise LLMError(f"LLM rank evaluation failed: {exc}") from exc

    # Compute overall score and verdict
    overall = compute_overall_score(
        llm_output.technical_score,
        llm_output.experience_score,
        llm_output.behavioral_score,
        llm_output.career_score,
    )
    verdict = score_to_verdict(overall)

    # Upsert evaluation record (one evaluation per job_posting_id)
    existing_result = await db.execute(
        select(RankEvaluation).where(RankEvaluation.job_posting_id == job.id)
    )
    evaluation = existing_result.scalar_one_or_none()
    if evaluation is None:
        evaluation = RankEvaluation(
            job_posting_id=job.id,
            user_id=candidate.user_id,
        )
        db.add(evaluation)
    evaluation.technical_score = llm_output.technical_score
    evaluation.experience_score = llm_output.experience_score
    evaluation.behavioral_score = llm_output.behavioral_score
    evaluation.career_score = llm_output.career_score
    evaluation.overall_score = overall
    evaluation.verdict = verdict
    evaluation.location_status = llm_output.location_status
    evaluation.deadline = llm_output.deadline
    evaluation.deadline_urgent = llm_output.deadline_urgent
    evaluation.strengths = llm_output.strengths
    evaluation.gaps = llm_output.gaps
    evaluation.missing_keywords = llm_output.missing_keywords
    evaluation.red_flags = llm_output.red_flags
    evaluation.language = llm_output.language
    evaluation.raw_response = llm_output.model_dump()
    await db.flush()
    await db.refresh(evaluation)

    return evaluation


# ── Query helpers ───────────────────────────────────────────────────


async def get_rank_evaluation(
    db: AsyncSession, job_posting_id: str, user_id: str
) -> RankEvaluation:
    """Get the rank evaluation for a job posting."""
    with db.no_autoflush:
        result = await db.execute(
            select(RankEvaluation).where(
                RankEvaluation.job_posting_id == job_posting_id,
                RankEvaluation.user_id == user_id,
            )
        )
    evaluation = result.scalar_one_or_none()
    if evaluation is None:
        raise NotFoundError("Rank evaluation not found.")
    return evaluation


async def list_ranked_jobs(
    db: AsyncSession,
    user_id: str,
    min_score: int | None = None,
    verdict: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JobPosting]:
    """List ranked jobs with optional filters."""
    query = (
        select(JobPosting)
        .where(JobPosting.user_id == user_id, JobPosting.status == "ranked")
        .order_by(JobPosting.rank_score.desc().nullslast())
    )

    if min_score is not None:
        query = query.where(JobPosting.rank_score >= min_score)
    if verdict:
        query = query.where(JobPosting.rank_verdict == verdict)

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())
