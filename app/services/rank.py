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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import CandidateProfile, JobPosting, RankEvaluation, User
from app.exceptions import NotFoundError, ProfileIncompleteError, LLMError
from app.llm.adapter import llm_completion_structured
from app.schemas.rank import RankLLMOutput, RankResult, RankedJobOut
from app.schemas.scrape import JobPostingSummary

settings = get_settings()

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
    below_threshold = 0
    expired_or_vetoed = 0

    for job in jobs:
        try:
            evaluation = await _rank_single_job(db, candidate, job)
            ranked_jobs.append((job, evaluation))
        except LLMError:
            # If LLM fails, skip this job but continue with others
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
    for job, eval_ in ranked_jobs[:top_n]:
        shortlist.append(
            RankedJobOut(
                job=JobPostingSummary.model_validate(job),
                evaluation=eval_,
            )
        )

    for job, eval_ in ranked_jobs[top_n:]:
        if eval_.overall_score < 30 or eval_.location_status == "FAIL":
            expired_or_vetoed += 1
        else:
            below_threshold += 1

    # 6. Update job statuses
    for job, eval_ in ranked_jobs:
        job.status = "ranked"
        job.rank_score = eval_.overall_score
        job.rank_verdict = eval_.verdict
        job.rank_date = datetime.now(timezone.utc)

    await db.commit()

    return RankResult(
        ranked_count=len(ranked_jobs),
        shortlist=shortlist,
        below_threshold=below_threshold,
        expired_or_vetoed=expired_or_vetoed,
        message=f"Ranked {len(ranked_jobs)} jobs. Top {len(shortlist)} in shortlist.",
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
        query = query.where(JobPosting.status == "new")

    if focus_area:
        query = query.where(
            JobPosting.title.ilike(f"%{focus_area}%")
            | JobPosting.description.ilike(f"%{focus_area}%")
        )

    query = query.order_by(JobPosting.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def _rank_single_job(
    db: AsyncSession,
    candidate: CandidateProfile,
    job: JobPosting,
) -> RankEvaluation:
    """Rank a single job posting against the candidate profile."""
    messages = build_rank_prompt(candidate, job)

    # Call LLM with structured output
    try:
        llm_output: RankLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=RankLLMOutput,
            provider=settings.llm_default_provider,
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

    # Create evaluation record
    evaluation = RankEvaluation(
        job_posting_id=job.id,
        user_id=candidate.user_id,
        technical_score=llm_output.technical_score,
        experience_score=llm_output.experience_score,
        behavioral_score=llm_output.behavioral_score,
        career_score=llm_output.career_score,
        overall_score=overall,
        verdict=verdict,
        location_status=llm_output.location_status,
        deadline=llm_output.deadline,
        deadline_urgent=llm_output.deadline_urgent,
        strengths=llm_output.strengths,
        gaps=llm_output.gaps,
        missing_keywords=llm_output.missing_keywords,
        red_flags=llm_output.red_flags,
        language=llm_output.language,
        raw_response=llm_output.model_dump(),
    )

    db.add(evaluation)
    await db.flush()
    await db.refresh(evaluation)

    return evaluation


# ── Query helpers ───────────────────────────────────────────────────


async def get_rank_evaluation(
    db: AsyncSession, job_posting_id: str, user_id: str
) -> RankEvaluation:
    """Get the rank evaluation for a job posting."""
    result = await db.execute(
        select(RankEvaluation)
        .join(JobPosting, RankEvaluation.job_posting_id == JobPosting.id)
        .where(
            RankEvaluation.job_posting_id == job_posting_id,
            JobPosting.user_id == user_id,
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