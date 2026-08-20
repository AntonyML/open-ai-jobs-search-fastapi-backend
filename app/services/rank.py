"""Rank service — evaluates job postings against the candidate profile.

REFACTORED: Uses LLMOrchestrator (with failover, retries, health tracking)
+ deterministic RankAnalyzer for quantitative scores.
The LLM now only handles qualitative reasoning (strengths, gaps, red flags).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import bind_context, get_logger
from app.core.settings import get_settings
from app.db.models import CandidateProfile, JobPosting, RankEvaluation
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.schemas.rank import JobPostingSummary, RankedJobOut, RankQualitativeOutput, RankResult
from app.services.orchestrator.llm_response_sanitizer import default_field_constraints
from app.services.orchestrator.orchestrator_deps import get_orchestrator
from app.services.provider_config import get_active_provider_config
from app.services.rank_analyzer import compute_quantitative_scores
from app.services.salary import service as salary_service

settings = get_settings()
logger = get_logger(__name__)

# ── Version pinning (Fase 5) ─────────────────────────────────────────

PROMPT_VERSION = "2.0.0"  # bumped when prompt changes — stored in RankEvaluationVersion
ALGORITHM_VERSION = "2.0.0"


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
    quantitative: dict[str, Any],  # kept for signature compat; not leaked to LLM
) -> list[dict[str, str]]:
    """Build the messages for the LLM rank evaluation.

    The LLM now only produces qualitative fields (behavioral_score,
    career_score, strengths, gaps, red_flags, confidence).
    Quantitative scores are computed server-side (Fase 4) and merged
    after the LLM call — the LLM does NOT see them.
    """
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    system_prompt = f"""{GUARDRAIL_SYSTEM_PROMPT}

You are an expert technical recruiter evaluating a candidate's fit for a specific role.
Focus on qualitative reasoning that cannot be automated.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

YOUR TASK — qualitative reasoning only:
1. **Behavioral score** (0-100): How well does the candidate's work style match the role?
2. **Career score** (0-100): How well does this role advance the candidate's career goals?
3. **Strengths** (max 5): Strongest qualitative reasons this candidate is a good fit
4. **Gaps** (max 5): Honest qualitative gaps NOT captured by keyword matching
5. **Red flags** (max 3): Things a recruiter would notice negatively in first 10 seconds
6. **Confidence** ("low"|"medium"|"high"): How confident are you in this evaluation?

Return ONLY valid JSON matching the RankQualitativeOutput schema:
{{"behavioral_score": int, "career_score": int, "strengths": [...], "gaps": [...], "red_flags": [...], "confidence": "medium"}}
Quantitative scores are computed server-side — do NOT include them in your response.
"""  # noqa: E501

    user_prompt = "Provide your qualitative evaluation. Return JSON with behavioral_score, career_score, strengths, gaps, red_flags, and confidence."  # noqa: E501

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
            parts.append(
                "Technical Skills: "
                + ", ".join(
                    f"{s.get('language', '')} ({s.get('proficiency', '')})" for s in skills["programming_ml"][:5]
                )
            )
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
    return round(technical * 0.30 + experience * 0.25 + behavioral * 0.15 + career * 0.30)


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
    db_factory,
    user_id: str,
    focus_area: str | None = None,
    re_rank: bool = False,
    top_n: int = 5,
    max_jobs: int | None = None,
) -> RankResult:
    """Execute a rank run for the user's job postings.

    ─── 3-phase lifecycle ──────────────────────────────────────
    1. LOAD — 1 sesión corta: candidate, jobs, existing evals
    2. RANK — puro en memoria + LLM (0 queries DB)
    3. SAVE — 1 sesión batch: upsert evaluations + job statuses
    ────────────────────────────────────────────────────────────
    """
    with bind_context(stage="rank"):
        # ── Phase 1: LOAD (single short session) ─────────────────
        async with db_factory() as db:
            candidate = await _get_candidate_profile(db, user_id)
            provider_config = await get_active_provider_config(db)

            jobs = await _select_jobs_to_rank(db, user_id, focus_area, re_rank, max_jobs=max_jobs)
            if not jobs:
                return RankResult(
                    ranked_count=0,
                    shortlist=[],
                    below_threshold=0,
                    expired_or_vetoed=0,
                    message="No new jobs to rank.",
                )

            existing_evals = {
                ev.job_posting_id: ev
                for ev in (
                    await db.execute(
                        select(RankEvaluation).where(RankEvaluation.job_posting_id.in_([j.id for j in jobs]))
                    )
                )
                .scalars()
                .all()
            }

        # ── Phase 2: RANK (pure computation + LLM, 0 DB queries) ──
        ranked_results: list[dict[str, Any]] = []
        failed_jobs = 0

        for index, job in enumerate(jobs, start=1):
            logger.info("Evaluating job %d/%d: %s", index, len(jobs), job.id)
            try:
                ev_data = await _rank_single_job(
                    candidate=candidate,
                    job=job,
                    provider_config=provider_config,
                    user_id=user_id,
                    existing_evaluation=existing_evals.get(job.id),
                )
                ranked_results.append(ev_data)
                logger.info("Finished job %d/%d: %s", index, len(jobs), job.id)
            except LLMError as exc:
                failed_jobs += 1
                logger.warning("LLM ranking failed for job %s: %s", job.id, exc)
                continue

        if not ranked_results:
            return RankResult(
                ranked_count=0,
                shortlist=[],
                below_threshold=0,
                expired_or_vetoed=0,
                message="All jobs failed.",
            )

        # ── Phase 3: SAVE (single batch session) ──────────────────
        async with db_factory() as db:
            saved_evaluations: list[tuple[JobPosting, RankEvaluation]] = []
            for ev_data in ranked_results:
                job = await db.get(JobPosting, ev_data["job_id"])
                cand = await db.get(CandidateProfile, candidate.id)
                evaluation = await _build_rank_evaluation(
                    db=db,
                    candidate=cand,
                    job=job,
                    user_id=user_id,
                    quantitative=ev_data["quantitative"],
                    llm_output=ev_data["llm_output"],
                    provider_config=provider_config,
                    existing_evaluation=ev_data.get("existing_evaluation"),
                    technical_score=ev_data["technical_score"],
                    experience_score=ev_data["experience_score"],
                    behavioral_score=ev_data["behavioral_score"],
                    career_score=ev_data["career_score"],
                    overall=ev_data["overall"],
                    verdict=ev_data["verdict"],
                    location_status=ev_data["location_status"],
                    deadline=ev_data.get("deadline"),
                    deadline_urgent=ev_data["deadline_urgent"],
                    strengths=ev_data.get("strengths"),
                    gaps=ev_data.get("gaps"),
                    missing_keywords=ev_data.get("missing_keywords"),
                    red_flags=ev_data.get("red_flags"),
                    language=ev_data.get("language") or job.language,
                    technical_fit=ev_data.get("quantitative", {}).get("technical_fit"),
                    relevant_experience=ev_data.get("quantitative", {}).get("relevant_experience"),
                    constraints_fit=ev_data.get("quantitative", {}).get("constraints_fit"),
                    career_alignment=ev_data.get("quantitative", {}).get("career_alignment"),
                    behavioral_fit=ev_data.get("quantitative", {}).get("behavioral_fit"),
                )
                job.status = "ranked"
                job.rank_score = evaluation.overall_score
                job.rank_verdict = evaluation.verdict
                job.rank_date = datetime.now(UTC)
                saved_evaluations.append((job, evaluation))

            # Sort for shortlist
            saved_evaluations.sort(
                key=lambda x: (x[1].overall_score, x[1].deadline_urgent),
                reverse=True,
            )

            # Salary benchmarks
            salary_available = False
            salary_company_count = 0
            salary_data = None
            try:
                salary_data = await salary_service.get_user_salary_data(db, user_id)
                salary_available = salary_data is not None
                salary_company_count = len(salary_data.get("companies", [])) if salary_data else 0
            except Exception as exc:
                logger.debug("Salary data unavailable for user %s: %s", user_id, exc)

            shortlist = []
            below_threshold = 0
            expired_or_vetoed = 0
            for job, eval_ in saved_evaluations:
                salary_benchmark = None
                if salary_available and job.company:
                    try:
                        salary_benchmark = await salary_service.benchmark_job(
                            db=db,
                            user_id=user_id,
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

            await db.commit()

        logger.info("Successful: %d, failed: %d", len(ranked_results), failed_jobs)
        return RankResult(
            ranked_count=len(ranked_results),
            shortlist=shortlist,
            below_threshold=below_threshold,
            expired_or_vetoed=expired_or_vetoed,
            message=f"Ranked {len(ranked_results)} jobs. Top {len(shortlist)} in shortlist. {failed_jobs} failed.",
            salary_data_available=salary_available,
            salary_data_company_count=salary_company_count,
        )


async def _get_candidate_profile(db: AsyncSession, user_id: str) -> CandidateProfile:
    """Get the candidate profile, raising if incomplete."""
    result = await db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user_id).options(selectinload(CandidateProfile.user))
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise ProfileIncompleteError("Candidate profile not found. Run /setup/profile first.")
    # Check for minimum required fields
    if not profile.full_name or not profile.experience:
        raise ProfileIncompleteError("Profile is incomplete. Please fill in at least name and experience.")
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


# ── Post-LLM validation (Fase 5) ────────────────────────────────────


def _validate_llm_output(raw: Any) -> RankQualitativeOutput:
    """Validate LLM output with strict rules.

    - Must be valid JSON
    - Scores must be int 0-100
    - List lengths within max
    - No claims without evidence (strengths/gaps must be non-trivial)
    - confidence must be low/medium/high

    Raises ValueError with details if validation fails.
    """
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON from LLM: {e}") from e
        return _validate_output_dict(data)

    if isinstance(raw, dict):
        return _validate_output_dict(raw)

    if isinstance(raw, RankQualitativeOutput):
        return _validate_output_instance(raw)

    raise ValueError(f"Unexpected LLM output type: {type(raw).__name__}")


def _validate_output_dict(data: dict) -> RankQualitativeOutput:
    """Validate a dict against RankQualitativeOutput rules."""
    behavioral = data.get("behavioral_score")
    career = data.get("career_score")
    if not isinstance(behavioral, int) or not (0 <= behavioral <= 100):
        raise ValueError(f"behavioral_score must be int 0-100, got {behavioral!r}")
    if not isinstance(career, int) or not (0 <= career <= 100):
        raise ValueError(f"career_score must be int 0-100, got {career!r}")

    strengths = data.get("strengths", [])
    gaps = data.get("gaps", [])
    red_flags = data.get("red_flags", [])
    _check_list_lengths(strengths, gaps, red_flags)

    confidence = data.get("confidence", "medium")
    if confidence not in ("low", "medium", "high"):
        raise ValueError(f"confidence must be low/medium/high, got {confidence!r}")

    return RankQualitativeOutput(
        behavioral_score=behavioral,
        career_score=career,
        strengths=strengths,
        gaps=gaps,
        red_flags=red_flags,
        confidence=confidence,
    )


def _validate_output_instance(qual: RankQualitativeOutput) -> RankQualitativeOutput:
    """Validate an already-parsed RankQualitativeOutput instance."""
    _check_list_lengths(qual.strengths, qual.gaps, qual.red_flags)
    return qual


def _check_list_lengths(strengths: list, gaps: list, red_flags: list):
    """Validate list lengths and trivial content."""
    if not isinstance(strengths, list) or len(strengths) > 5:
        raise ValueError(f"strengths must be a list of max 5, got {len(strengths)}")
    if not isinstance(gaps, list) or len(gaps) > 5:
        raise ValueError(f"gaps must be a list of max 5, got {len(gaps)}")
    if not isinstance(red_flags, list) or len(red_flags) > 3:
        raise ValueError(f"red_flags must be a list of max 3, got {len(red_flags)}")

    def _non_trivial(items: list[str]) -> bool:
        return all(len(s.strip()) >= 1 for s in items)

    if strengths and not _non_trivial(strengths):
        raise ValueError("strengths contain empty or trivial items")
    if gaps and not _non_trivial(gaps):
        raise ValueError("gaps contain empty or trivial items")


async def _rank_single_job(
    candidate: CandidateProfile,
    job: JobPosting,
    provider_config: dict[str, Any],
    user_id: str,
    existing_evaluation: RankEvaluation | None = None,
    llm_call_override: Callable | None = None,
) -> dict[str, Any]:
    """Rank a single job — pure computation + LLM, 0 DB queries.

    Returns a dict with all evaluation data for batch persistence.
    Post-LLM validation ensures no corrupt or partial evaluations persist.

    Args:
        candidate: CandidateProfile ORM object (loaded in Phase 1).
        job: JobPosting ORM object (loaded in Phase 1).
        existing_evaluation: Pre-loaded evaluation for upsert.
        llm_call_override: Optional async callable(messages, output_schema, provider_config)
            → raw JSON string. When None, uses the orchestrator.
    """
    # Step 1: Deterministic analysis (pure Python)
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
        "salary": job.salary,
    }

    quantitative = compute_quantitative_scores(candidate_dict, job_dict, candidate.job_target)

    # Hard reject (veto) — no LLM call needed
    if quantitative.get("_veto"):
        logger.info("Job %s vetoed: %s", job.id, quantitative.get("_veto_reason"))
        return _veto_result(job.id, quantitative, existing_evaluation)

    # Step 2: Build LLM prompt
    messages = build_rank_prompt(candidate, job, quantitative)

    # Step 3: Call LLM
    try:
        if llm_call_override is not None:
            raw = await llm_call_override(messages, RankQualitativeOutput, provider_config)
            qual = _validate_llm_output(raw)
        else:
            orchestrator = get_orchestrator()
            qual = await orchestrator.execute(
                user_id=user_id,
                messages=messages,
                output_schema=RankQualitativeOutput,
                pipeline="rank",
                description=f"Rank {job.title} at {job.company or 'Unknown'}",
                provider=provider_config.get("provider"),
                model=provider_config.get("model"),
                temperature=0.3,
                max_tokens=1536,
                field_constraints=default_field_constraints(),
            )
            _validate_llm_output(qual)  # business rules only (lengths, trivial claims)

    except Exception as e:
        logger.error("LLM call or validation failed for job %s: %s", job.id, e)
        raise LLMError(f"LLM evaluation failed for job {job.id}: {e}") from e

    # Step 3c: Confidence penalty
    confidence = qual.confidence
    behavioral_score = qual.behavioral_score
    career_score = qual.career_score
    if confidence == "low":
        behavioral_score = int(behavioral_score * 0.7)
        career_score = int(career_score * 0.7)
        logger.info("Confidence=low for job %s — penalized scores by 30%%", job.id)

    # Step 4: Merge deterministic + LLM scores
    technical_score = quantitative["technical_score"]
    experience_score = quantitative["experience_score"]

    overall = compute_overall_score(
        technical_score,
        experience_score,
        behavioral_score,
        career_score,
    )
    verdict = score_to_verdict(overall)

    return {
        "job_id": job.id,
        "quantitative": quantitative,
        "llm_output": qual,
        "existing_evaluation": existing_evaluation,
        "technical_score": technical_score,
        "experience_score": experience_score,
        "behavioral_score": behavioral_score,
        "career_score": career_score,
        "overall": overall,
        "verdict": verdict,
        "location_status": quantitative["location_status"],
        "deadline": quantitative["deadline"],
        "deadline_urgent": quantitative["deadline_urgent"],
        "strengths": qual.strengths,
        "gaps": qual.gaps,
        "missing_keywords": quantitative["missing_keywords"],
        "red_flags": qual.red_flags,
        "language": quantitative["language"] or job.language,
        "confidence": confidence,
    }


def _veto_result(
    job_id: str,
    quantitative: dict[str, Any],
    existing_evaluation: RankEvaluation | None = None,
) -> dict[str, Any]:
    """Build result dict for a vetoed job (no LLM needed)."""
    reason = quantitative.get("_veto_reason", "Vetoed")
    return {
        "job_id": job_id,
        "quantitative": quantitative,
        "llm_output": RankQualitativeOutput(
            behavioral_score=0,
            career_score=0,
            strengths=[],
            gaps=[],
            red_flags=[reason],
            confidence="high",
        ),
        "existing_evaluation": existing_evaluation,
        "technical_score": quantitative["technical_score"],
        "experience_score": quantitative["experience_score"],
        "behavioral_score": 0,
        "career_score": 0,
        "overall": 0,
        "verdict": "Poor Fit",
        "location_status": quantitative["location_status"],
        "deadline": quantitative["deadline"],
        "deadline_urgent": quantitative["deadline_urgent"],
        "strengths": [],
        "gaps": [],
        "missing_keywords": quantitative["missing_keywords"],
        "red_flags": [reason],
        "language": quantitative["language"],
        "confidence": "high",
    }


async def _build_rank_evaluation(
    db: AsyncSession,
    candidate: CandidateProfile,
    job: JobPosting,
    user_id: str,
    quantitative: dict[str, Any],
    llm_output: RankQualitativeOutput | dict,
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
    # Fase 4 structured dimensions
    technical_fit: dict | None = None,
    relevant_experience: dict | None = None,
    constraints_fit: dict | None = None,
    career_alignment: dict | None = None,
    behavioral_fit: dict | None = None,
) -> RankEvaluation:
    """Persist (upsert) a rank evaluation record."""
    # Merge existing evaluation from a potentially different session (worker flow)
    # into the current session to avoid "not persistent" errors on db.refresh()
    if existing_evaluation is not None:
        evaluation = await db.merge(existing_evaluation)
    else:
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
    evaluation.technical_fit = technical_fit
    evaluation.relevant_experience = relevant_experience
    evaluation.constraints_fit = constraints_fit
    evaluation.career_alignment = career_alignment
    evaluation.behavioral_fit = behavioral_fit
    evaluation.raw_response = {
        "quantitative": quantitative,
        "llm_qualitative": llm_output.model_dump() if hasattr(llm_output, "model_dump") else {},
    }
    await db.flush()
    await db.refresh(evaluation)

    return evaluation


# ── Query helpers ───────────────────────────────────────────────────


async def get_rank_evaluation(db: AsyncSession, job_posting_id: str, user_id: str) -> RankEvaluation:
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

    total_subq = select(func.count()).select_from(JobPosting).where(JobPosting.user_id == user_id)
    if not re_rank:
        total_subq = total_subq.where(
            or_(
                JobPosting.status == "new",
                JobPosting.rank_score.is_(None),
            )
        )

    ranked_subq = (
        select(func.count())
        .select_from(JobPosting)
        .where(
            JobPosting.user_id == user_id,
            JobPosting.status == "ranked",
            JobPosting.rank_score.isnot(None),
        )
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
