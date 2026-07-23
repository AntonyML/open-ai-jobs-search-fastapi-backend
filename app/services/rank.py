"""Rank service — evaluates job postings against the candidate profile.

REFACTORED: Uses LLMOrchestrator (with failover, retries, health tracking)
+ deterministic RankAnalyzer for quantitative scores.
The LLM now only handles qualitative reasoning (strengths, gaps, red flags).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.settings import get_settings
from app.db.models import CandidateProfile, JobPosting, RankEvaluation, User
from app.exceptions import NotFoundError, ProfileIncompleteError, LLMError
from app.services.rank_analyzer import compute_quantitative_scores
from app.services.orchestrator.orchestrator_deps import get_orchestrator
from app.services.orchestrator.llm_response_sanitizer import default_field_constraints
from app.schemas.rank import RankLLMOutput, RankResult, RankedJobOut
from app.schemas.scrape import JobPostingSummary
from app.services.provider_credentials import get_user_active_provider_config
from app.services.salary import service as salary_service

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
    quantitative: dict[str, Any],
) -> list[dict[str, str]]:
    """Build the messages for the LLM rank evaluation.

    The LLM now only needs to reason about:
    - behavioral_score (0-100)
    - career_score (0-100)
    - strengths (max 3)
    - gaps (max 3)
    - red_flags (max 3)

    Everything else is pre-computed by the deterministic rank analyzer.
    """
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    missing_kw_text = ", ".join(quantitative.get("missing_keywords", [])[:5]) or "None detected"

    system_prompt = f"""{GUARDRAIL_SYSTEM_PROMPT}

You are an expert technical recruiter evaluating a candidate's fit for a specific role.
Focus on qualitative reasoning that cannot be automated.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

PRE-COMPUTED ANALYSIS (deterministic — do NOT override):
- Technical score: {quantitative.get('technical_score', 50)}/100
- Experience score: {quantitative.get('experience_score', 50)}/100
- Location: {quantitative.get('location_status', 'FLAG')}
- Language: {quantitative.get('language', 'en')}
- Missing keywords (algorithmic): {missing_kw_text}

YOUR TASK — qualitative reasoning only:
1. **Behavioral score** (0-100): How well does the candidate's work style match the role?
2. **Career score** (0-100): How well does this role advance career goals?
3. **Strengths** (max 3): Strongest qualitative reasons this candidate is a good fit
4. **Gaps** (max 3): Honest qualitative gaps NOT captured by keyword matching
5. **Red flags** (max 3): Things a recruiter would notice negatively in first 10 seconds

Return ONLY valid JSON matching the RankLLMOutput schema.
The quantitative scores above will be merged automatically — do NOT repeat them.
"""

    user_prompt = "Provide your qualitative evaluation. Return JSON with behavioral_score, career_score, strengths, gaps, and red_flags."

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
    max_jobs: int | None = None,
) -> RankResult:
    """Execute a rank run for the user's job postings.

    Args:
        db: Database session.
        user_id: The authenticated user's ID.
        focus_area: Optional filter for job title/description.
        re_rank: If True, re-evaluate already-ranked jobs.
        top_n: Size of the shortlist to return.
        max_jobs: Max jobs to rank (None = unlimited, used for free-tier limit).

    Returns:
        RankResult with shortlist and counts.
    """
    # 1. Get candidate profile
    candidate = await _get_candidate_profile(db, user_id)
    provider_config = await get_user_active_provider_config(db, user_id)

    # 2. Select jobs to rank
    jobs = await _select_jobs_to_rank(db, user_id, focus_area, re_rank, max_jobs=max_jobs)

    if not jobs:
        return RankResult(
            ranked_count=0,
            shortlist=[],
            below_threshold=0,
            expired_or_vetoed=0,
            message="No new jobs to rank.",
        )

    # 3. Batch-load existing RankEvaluations for all jobs (avoids N+1)
    job_ids = [job.id for job in jobs]
    existing_result = await db.execute(
        select(RankEvaluation).where(RankEvaluation.job_posting_id.in_(job_ids))
    )
    existing_evals = {
        ev.job_posting_id: ev for ev in existing_result.scalars().all()
    }

    # 4. Rank each job
    ranked_jobs = []
    failed_jobs = 0
    below_threshold = 0
    expired_or_vetoed = 0

    for index, job in enumerate(jobs, start=1):
        logger.info("Evaluating job %d/%d: %s", index, len(jobs), job.id)
        try:
            evaluation = await _rank_single_job(
                db=db, candidate=candidate, job=job,
                provider_config=provider_config, user_id=user_id,
                existing_evaluation=existing_evals.get(job.id),
            )
            ranked_jobs.append((job, evaluation))
            logger.info("Finished job %d/%d: %s", index, len(jobs), job.id)

            # Commit every 5 jobs to prevent long-running transaction timeouts
            if index % 5 == 0:
                await db.commit()
                logger.debug("Intermediate commit at job %d/%d", index, len(jobs))

        except LLMError as exc:
            failed_jobs += 1
            logger.warning("LLM ranking failed for job %s: %s", job.id, exc)
            continue

    # 5. Sort by overall score (desc), deadline urgency as tiebreaker
    ranked_jobs.sort(
        key=lambda x: (
            x[1].overall_score,
            x[1].deadline_urgent,
        ),
        reverse=True,
    )

    # 6. Build shortlist and counts — with salary benchmarks
    # NOTE: Salary lookup is best-effort.  If the UserSalaryData table
    # doesn't exist yet (e.g. migration not run), we silently skip.
    salary_available = False
    salary_company_count = 0
    try:
        salary_data = await salary_service.get_user_salary_data(db, user_id)
        salary_available = salary_data is not None
        salary_company_count = len(salary_data.get("companies", [])) if salary_data else 0
    except Exception as exc:
        logger.debug("Salary data unavailable for user %s: %s", user_id, exc)

    shortlist = []
    for job, eval_ in ranked_jobs:
        salary_benchmark = None
        if salary_available and job.company:
            try:
                salary_benchmark = await salary_service.benchmark_job(
                    db=db, user_id=user_id,
                    salary_data=salary_data,
                    company_name=job.company,
                    job_title=job.title,
                    job_location=job.location,
                )
            except Exception as exc:
                logger.debug("Salary lookup failed for %s: %s", job.company, exc)

        if eval_.location_status == "FAIL":
            expired_or_vetoed += 1
        elif len(shortlist) < top_n:
            shortlist.append(
                RankedJobOut(
                    job=JobPostingSummary.model_validate(job),
                    evaluation=eval_,
                    salary=salary_benchmark,
                )
            )
        elif eval_.overall_score < 30:
            below_threshold += 1
        else:
            expired_or_vetoed += 1

    # 7. Update job statuses
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
        salary_data_available=salary_available,
        salary_data_company_count=salary_company_count,
    )


async def _get_candidate_profile(db: AsyncSession, user_id: str) -> CandidateProfile:
    """Get the candidate profile, raising if incomplete."""
    result = await db.execute(
        select(CandidateProfile)
        .where(CandidateProfile.user_id == user_id)
        .options(selectinload(CandidateProfile.user))
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
    max_jobs: int | None = None,
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
    if max_jobs is not None:
        query = query.limit(max_jobs)
    logger.info("Rank SQL: %s", query)
    result = await db.execute(query)
    jobs = list(result.scalars().all())
    logger.info("Rank jobs selected: %d (max_jobs=%s)", len(jobs), max_jobs)
    return jobs


async def _rank_single_job(
    db: AsyncSession,
    candidate: CandidateProfile,
    job: JobPosting,
    provider_config: dict[str, Any],
    user_id: str,
    existing_evaluation: RankEvaluation | None = None,
) -> RankEvaluation:
    """Rank a single job posting against the candidate profile.
    Merges deterministic quant scores with LLM qualitative reasoning.

    Args:
        existing_evaluation: Pre-loaded evaluation to avoid N+1 queries.
            Callers should batch-load all evaluations before the loop.
    """
    # Step 1: Deterministic analysis (no LLM needed)
    candidate_dict = {
        "skills": candidate.skills,
        "experience": candidate.experience,
        "location": candidate.location,
        "constraints": candidate.constraints,
    }
    job_dict = {
        "title": job.title,
        "description": job.description,
        "requirements": job.requirements,
        "location": job.location,
        "deadline": job.deadline,
        "language": job.language,
    }

    quantitative = compute_quantitative_scores(candidate_dict, job_dict, candidate.job_target)

    # Step 2: Build the LLM prompt with pre-computed quantitative data
    messages = build_rank_prompt(candidate, job, quantitative)

    # Step 3: Call LLM through orchestrator for qualitative reasoning
    orchestrator = get_orchestrator()

    llm_output: RankLLMOutput = await orchestrator.execute(
        user_id=user_id,
        messages=messages,
        output_schema=RankLLMOutput,
        pipeline="rank",
        description=f"Rank {job.title} at {job.company or 'Unknown'}",
        provider=provider_config.get("provider"),
        model=provider_config.get("model"),
        temperature=0.3,
        max_tokens=1536,
        field_constraints=default_field_constraints(),
    )

    # Step 4: Merge deterministic scores with LLM qualitative scores
    technical_score = quantitative["technical_score"]
    experience_score = quantitative["experience_score"]
    behavioral_score = llm_output.behavioral_score
    career_score = llm_output.career_score

    # Possible veto from job_target filtering
    if quantitative.get("_veto"):
        logger.info("Job %s vetoed: %s", job.id, quantitative.get("_veto_reason"))
        llm_output = RankLLMOutput(
            technical_score=0,
            experience_score=0,
            behavioral_score=0,
            career_score=0,
            overall_score=0,
            strengths=[],
            gaps=[],
            red_flags=[quantitative.get("_veto_reason", "Vetoed")],
            recommendation="reject",
        )
        return _build_rank_evaluation(
            db=db,
            candidate=candidate,
            job=job,
            user_id=user_id,
            quantitative=quantitative,
            llm_output=llm_output,
            provider_config=provider_config,
            existing_evaluation=existing_evaluation,
        )

    location_status = quantitative["location_status"]
    deadline = quantitative["deadline"]
    deadline_urgent = quantitative["deadline_urgent"]
    language = quantitative["language"]
    missing_keywords = quantitative["missing_keywords"]

    strengths = llm_output.strengths
    gaps = llm_output.gaps
    red_flags = llm_output.red_flags

    # Compute overall score
    overall = compute_overall_score(
        technical_score, experience_score, behavioral_score, career_score,
    )
    verdict = score_to_verdict(overall)

    # Step 5: Upsert evaluation record (use pre-loaded if available to avoid N+1)
    return await _build_rank_evaluation(
        db=db,
        candidate=candidate,
        job=job,
        user_id=user_id,
        quantitative=quantitative,
        llm_output=llm_output,
        provider_config=provider_config,
        existing_evaluation=existing_evaluation,
        technical_score=technical_score,
        experience_score=experience_score,
        behavioral_score=behavioral_score,
        career_score=career_score,
        overall=overall,
        verdict=verdict,
        location_status=location_status,
        deadline=deadline,
        deadline_urgent=deadline_urgent,
        strengths=strengths,
        gaps=gaps,
        missing_keywords=missing_keywords,
        red_flags=red_flags,
        language=language or job.language,
    )


async def _build_rank_evaluation(
    db: AsyncSession,
    candidate: CandidateProfile,
    job: JobPosting,
    user_id: str,
    quantitative: dict[str, Any],
    llm_output: RankLLMOutput,
    provider_config: dict[str, Any],
    existing_evaluation: RankEvaluation | None = None,
    technical_score: int = 0,
    experience_score: int = 0,
    behavioral_score: int = 0,
    career_score: int = 0,
    overall: int = 0,
    verdict: str = "Poor Fit",
    location_status: str = "FLAG",
    deadline: str | None = None,
    deadline_urgent: bool = False,
    strengths: list[str] | None = None,
    gaps: list[str] | None = None,
    missing_keywords: list[str] | None = None,
    red_flags: list[str] | None = None,
    language: str | None = None,
) -> RankEvaluation:
    """Persist (upsert) a rank evaluation record."""
    evaluation = existing_evaluation
    if evaluation is None:
        evaluation = RankEvaluation(
            job_posting_id=job.id,
            user_id=candidate.user_id,
        )
        db.add(evaluation)

    evaluation.technical_score = technical_score
    evaluation.experience_score = experience_score
    evaluation.behavioral_score = behavioral_score
    evaluation.career_score = career_score
    evaluation.overall_score = overall
    evaluation.verdict = verdict
    evaluation.location_status = location_status
    evaluation.deadline = deadline
    evaluation.deadline_urgent = deadline_urgent
    evaluation.strengths = strengths or []
    evaluation.gaps = gaps or []
    evaluation.missing_keywords = missing_keywords or []
    evaluation.red_flags = red_flags or []
    evaluation.language = language or ""
    evaluation.raw_response = {
        "quantitative": quantitative,
        "llm_qualitative": llm_output.model_dump() if hasattr(llm_output, "model_dump") else {},
    }
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


async def count_jobs_to_rank(
    db: AsyncSession,
    user_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Count total unranked jobs and already ranked jobs for a user.

    Args:
        db: Database session.
        user_id: The authenticated user's ID.
        payload: Optional rank request parameters (focus_area, re_rank).

    Returns:
        Dict with total, ranked, and unranked counts.
    """
    re_rank = (payload or {}).get("re_rank", False) if payload else False

    total_subq = select(func.count()).select_from(JobPosting).where(
        JobPosting.user_id == user_id
    )
    if not re_rank:
        total_subq = total_subq.where(
            or_(
                JobPosting.status == "new",
                JobPosting.rank_score.is_(None),
            )
        )

    ranked_subq = select(func.count()).select_from(JobPosting).where(
        JobPosting.user_id == user_id,
        JobPosting.status == "ranked",
        JobPosting.rank_score.isnot(None),
    )

    stmt = select(
        func.coalesce(total_subq.scalar_subquery(), 0).label("total"),
        func.coalesce(ranked_subq.scalar_subquery(), 0).label("ranked"),
    )
    row = (await db.execute(stmt)).one()
    total = row.total
    ranked = row.ranked

    return {
        "total": total,
        "ranked": ranked,
        "unranked": total - ranked,
    }


async def list_ranked_jobs(
    db: AsyncSession,
    user_id: str,
    min_score: int | None = None,
    verdict: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JobPosting]:
    """List ranked jobs with optional filters. Returns both ranked and unranked (new) jobs."""
    query = (
        select(JobPosting)
        .where(JobPosting.user_id == user_id, JobPosting.status.in_(["new", "ranked"]))
        .order_by(JobPosting.rank_score.desc().nullslast())
    )

    if min_score is not None:
        query = query.where(JobPosting.rank_score >= min_score)
    if verdict:
        query = query.where(JobPosting.rank_verdict == verdict)

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())
