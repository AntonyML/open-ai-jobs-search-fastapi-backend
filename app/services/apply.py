"""Apply service — generates tailored CV and cover letter via JSON/Typst pipeline.

Implements the /apply workflow with a 3-stage Drafter-Reviewer pipeline:
1. DRAFT: Generate tailored experience + cover letter (JSON schema)
2. REVIEW: Second LLM call critiques the rendered drafts (fresh context)
3. REVISE: Apply review feedback and regenerate
4. COMPILE: Typst compilation and verification

Architecture decision:
We use SEPARATE LLM calls for draft, review, and revise so that:
- The reviewer has a fresh context window (no bias from the draft prompt)
- Each stage can use different temperature (draft=0.3, review=0.0 for reproducibility, revise=0.2)
- The review stage explicitly checks for fabricated content, missing keywords, and weak framing
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger, bind_context
logger = get_logger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import Application, CandidateProfile, JobPosting, RankEvaluation, User
from app.db.session import async_session_factory
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.llm.adapter import llm_completion, llm_completion_structured, get_provider_kwargs
from app.services import ats_check
from app.schemas.apply import (
    AddressedRedFlag,
    ApplyResult,
    IncorporatedKeyword,
    TailoredExperienceEntry,
)

settings = get_settings()

# ── Guardrail constant (never user-configurable) ────────────────────

APPLY_GUARDRAIL = """
IMPORTANT GUARDRAIL: You are tailoring a candidate's CV and cover letter for a specific job.
You MUST NEVER invent, hallucinate, or assume experience, titles, companies, or skills
that the candidate does not explicitly have in their profile.

Your role is to:
- REFRAME existing experience using the X-Y-Z formula: "Accomplished X, as measured by Y, by doing Z"
- Incorporate missing keywords ONLY where they are genuinely true for the candidate's real experience
- Address red flags by honestly reframing, not by hiding or inventing
- Keep all claims defensible in an interview (the "interview backtrack test")

If a missing keyword does not match the candidate's actual experience, DO NOT include it.
If a red flag cannot be honestly addressed, note it but do not fabricate a mitigation.
Every bullet must pass: "Could the candidate comfortably explain this in an interview without backtracking?"
"""

# ── X-Y-Z Formula guidance ──────────────────────────────────────────

XYZ_GUIDANCE = """
Use the X-Y-Z formula for every experience bullet:
- X = What you accomplished (specific, quantified result)
- Y = How it was measured (metric, KPI, scale)
- Z = How you did it (method, technology, approach)

Examples:
- "Reduced model inference latency by 40% (Y), as measured by p99 latency (Y), by implementing TensorRT optimization and batching (Z)"
- "Increased recommendation click-through rate by 15% (X), measured via A/B test (Y), by adding collaborative filtering signals to the ranking model (Z)"
- "Led a team of 5 engineers (Z) to deliver a real-time fraud detection system (X) processing 10K transactions/sec with <50ms latency (Y)"

Every bullet must have concrete numbers where possible. No vague claims.
"""

# ── Drafter-Reviewer prompt constants (temperature 0 for reproducibility) ─

REVIEWER_GUARDRAIL = """
You are a CRITICAL REVIEWER evaluating a draft CV and cover letter for a job application.
Your job is to catch issues BEFORE the documents are sent to a recruiter.

Rules:
1. NEVER accuse the draft of fabricating content unless you can specifically point to a claim
   that does not appear in the candidate's actual profile.
2. Compare EVERY bullet in the CV against the candidate's real experience. Flag any that
   contain skills, achievements, or credentials not present in the profile.
3. Check that keywords from the job posting that ARE genuinely true for the candidate
   have been incorporated. List any that are still missing.
4. Identify generic bullets that don't use the X-Y-Z formula properly (missing metrics,
   vague language, passive voice).
5. Verify the profile statement mentions the specific role title from the job posting.
6. Check cover letter for the required structure: opening, body with bullets, company
   connection, personal fit, closing.
7. Verify no em-dashes, cliches, or unverified company claims in the cover letter.

Output structured JSON with ReviewFeedback schema. Be specific and actionable.
"""

REVISE_GUARDRAIL = """
You are a SKILLED EDITOR applying reviewer feedback to improve a draft CV and cover letter.
Your job is to fix every issue the reviewer identified while maintaining factual accuracy.

Rules:
1. Only change what the reviewer flagged. Don't introduce new content beyond the fixes.
2. NEVER fabricate experience. If an issue cannot be fixed honestly, note it as a remaining concern.
3. Preserve the X-Y-Z formula structure throughout.
4. Maintain consistent tone between CV and cover letter.
5. If the reviewer flagged missing keywords, incorporate them ONLY where genuinely true.

Output the revised experience entries with the same TailoredExperienceLLMOutput schema
as the original draft, plus a separate ReviseResult describing the changes made.
"""


# ── JSON output examples for LLM prompts (plain strings, NOT f-strings) ──

_TAILORED_EXPERIENCE_JSON_EXAMPLE = """
    Return JSON with EXACTLY this structure (field names must match exactly):
    {
      "tailored_experience": [                                  // array, max 10 entries
        {
          "title": "string (job title, required)",
          "company": "string (company name, required)",
          "start_date": "string or null (e.g. '2020-03')",
          "end_date": "string or null (e.g. '2023-01')",
          "location": "string or null (e.g. 'San Francisco, CA')",
          "bullets": ["string (X-Y-Z formatted bullet, required)"]
        }
      ]
    }
"""

_COVER_LETTER_JSON_EXAMPLE = """
    Return JSON with EXACTLY this structure (field names must match exactly):
    {
      "opening_paragraph": "string (2-3 sentences, role + strongest connection)",
      "body_paragraphs": ["string (concrete bullets, max 4)"],
      "company_connection_paragraph": "string (why THIS company specifically)",
      "personal_fit_paragraph": "string (behavioral strengths, 2-3 sentences)",
      "closing_paragraph": "string (brief, confident, forward-looking)"
    }
"""

_REVIEW_JSON_EXAMPLE = """
    TASK:
Review these draft documents critically. Return JSON with EXACTLY this structure (field names must match exactly):
{
  "overall_assessment": "string (2-3 sentence summary of document quality)",
  "passes": ["string (things done well)"],
  "issues": [
    {
      "type": "string (one of: missing_keyword, generic_bullet, fabricated_claim, weak_framing, inconsistency, factual_error, formatting)",
      "description": "string (clear description of the issue)",
      "severity": "string (high, medium, or low)",
      "location": "string (cv, cover_letter, or both)",
      "suggestion": "string or null (how to fix this issue)"
    }
  ],
  "missed_keywords": ["string (keywords from job posting still absent after review)"],
  "strong_recommendations": ["string (top 3 changes that would most improve the application, ordered by impact)"]
}
"""

_REVISE_JSON_EXAMPLE = """
    Return the revised experience entries as JSON with EXACTLY this structure (field names must match exactly):
    {
      "tailored_experience": [                                  // array, max 10 entries
        {
          "title": "string (job title, required)",
          "company": "string (company name, required)",
          "start_date": "string or null (e.g. '2020-03')",
          "end_date": "string or null (e.g. '2023-01')",
          "location": "string or null (e.g. 'San Francisco, CA')",
          "bullets": ["string (X-Y-Z formatted bullet, required)"]
        }
      ]
    }
"""




# ── Prompt builders ─────────────────────────────────────────────────


def build_tailored_experience_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
) -> list[dict[str, str]]:
    """Build prompt for rewriting experience section with X-Y-Z formula."""

    candidate_summary = _build_candidate_summary_for_apply(candidate)
    job_summary = _build_job_summary_for_apply(job)
    missing_keywords = evaluation.missing_keywords or []
    red_flags = evaluation.red_flags or []

    # ── Job-target guidance ────────────────────────────────────
    jt_guidance = ""
    if candidate.job_target:
        jt = candidate.job_target
        parts = []
        if jt.get("target_titles"):
            parts.append(f"Primary target role: {jt['target_titles'][0]}")
        if jt.get("keywords"):
            parts.append(f"Priority keywords to highlight: {', '.join(jt['keywords'])}")
        if jt.get("min_salary") or jt.get("max_salary"):
            sal = []
            if jt.get("min_salary"): sal.append(f"min ${jt['min_salary']:,.0f}")
            if jt.get("max_salary"): sal.append(f"max ${jt['max_salary']:,.0f}")
            parts.append(f"Target salary range: {' – '.join(sal)}")
        if jt.get("seniority"):
            seniority = jt["seniority"]
            depth_map = {
                "junior": "emphasize fundamentals and learning",
                "mid": "balance fundamentals with applied experience",
                "senior": "emphasize architecture, mentoring, system design",
                "lead": "emphasize architecture, mentoring, system design",
                "manager": "emphasize team leadership, process, stakeholder management",
                "director": "emphasize strategic leadership, organizational impact",
                "executive": "emphasize strategic vision, cross-functional leadership",
            }
            depth = depth_map.get(seniority.lower(), "adjust technical depth accordingly")
            parts.append(f"Target seniority level: {seniority} — {depth}")
        if parts:
            jt_guidance = "\nJOB TARGET PREFERENCES (align experience toward these):\n" + "\n".join(f"- {p}" for p in parts)

    system_prompt = f"""{APPLY_GUARDRAIL}

{XYZ_GUIDANCE}

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

RANK EVALUATION INSIGHTS:
- Missing keywords (from job posting, absent in CV): {', '.join(missing_keywords) if missing_keywords else 'None'}
- Red flags (things recruiter would notice negatively): {', '.join(red_flags) if red_flags else 'None'}
- Overall fit: {evaluation.verdict} ({evaluation.overall_score}/100)
{jt_guidance}

TASK:
Rewrite the candidate's experience section for this specific job application.
1. Keep ALL existing roles, companies, dates, locations — only rewrite the bullets
2. Apply X-Y-Z formula to every bullet
3. Incorporate missing keywords ONLY where they are genuinely true for the candidate's experience
4. Address red flags by honest reframing (e.g., if "gap in employment" is a red flag, add a bullet about upskilling during that period if true)
5. Prioritize bullets that match the job's requirements
6. Maximum 4 bullets per role, 3 roles max in tailored CV

{_TAILORED_EXPERIENCE_JSON_EXAMPLE}
"""

    user_prompt = "Generate the tailored experience section for this job application."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_cover_letter_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
    tailored_experience: list[TailoredExperienceEntry],
) -> list[dict[str, str]]:
    """Build prompt for generating cover letter content."""

    candidate_summary = _build_candidate_summary_for_apply(candidate)
    job_summary = _build_job_summary_for_apply(job)

    # ── Job-target guidance ────────────────────────────────────
    jt_guidance = ""
    if candidate.job_target:
        jt = candidate.job_target
        parts = []
        if jt.get("target_titles"):
            parts.append(f"Primary role candidate is targeting: {jt['target_titles'][0]}")
        if jt.get("keywords"):
            parts.append(f"Emphasise these priority keywords when relevant: {', '.join(jt['keywords'])}")
        if parts:
            jt_guidance = "\nJOB TARGET PREFERENCES:\n" + "\n".join(f"- {p}" for p in parts)

    exp_lines = []
    for exp in tailored_experience:
        exp_lines.append(f"\n{exp.title} at {exp.company}")
        for bullet in exp.bullets:
            exp_lines.append(f"  • {bullet}")
    tailored_exp_text = "\n".join(exp_lines)

    system_prompt = f"""{APPLY_GUARDRAIL}

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

TAILORED EXPERIENCE (already generated, use as reference):
{tailored_exp_text}

RANK EVALUATION:
- Overall fit: {evaluation.verdict} ({evaluation.overall_score}/100)
- Missing keywords: {', '.join(evaluation.missing_keywords or [])}
- Red flags: {', '.join(evaluation.red_flags or [])}
{jt_guidance}

TASK:
Write a cover letter in the SAME LANGUAGE as the job posting ({job.language or 'en'}).
Follow the structure from the writing style guide:
1. Opening: Role + strongest connection (2-3 sentences)
2. Body: Most relevant experience + 3-5 concrete bullets (use tailored experience above)
3. Company connection: Why THIS company specifically (verified facts only)
4. Personal fit: Behavioral strengths + team contribution (2-3 sentences)
5. Closing: Brief, confident, forward-looking

Cover letter must be ~1 page when rendered with cover.cls template.
No em-dashes, no cliches, no unverified company claims.
Use "Claude Code" by name if mentioning AI tooling.

{_COVER_LETTER_JSON_EXAMPLE}
"""

    user_prompt = "Generate the cover letter content for this job application."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _build_candidate_summary_for_apply(candidate: CandidateProfile) -> str:
    """Build candidate summary for apply prompts."""
    parts = []

    if candidate.full_name:
        parts.append(f"Name: {candidate.full_name}")
    if candidate.location:
        parts.append(f"Location: {candidate.location}")
    if candidate.employment_status:
        parts.append(f"Status: {candidate.employment_status}")

    if candidate.education:
        edu_lines = []
        for edu in candidate.education[:2]:
            line = f"  - {edu.get('degree', 'Degree')} at {edu.get('institution', 'Institution')}"
            if edu.get("period"):
                line += f" ({edu['period']})"
            if edu.get("key_topics"):
                line += f" — {edu['key_topics']}"
            edu_lines.append(line)
        if edu_lines:
            parts.append("Education:\n" + "\n".join(edu_lines))

    if candidate.experience:
        exp_lines = []
        for exp in candidate.experience:
            line = f"  - {exp.get('title', 'Role')} at {exp.get('company', 'Company')}"
            if exp.get("start_date") or exp.get("end_date"):
                line += f" ({exp.get('start_date', '')}–{exp.get('end_date', 'Present')})"
            if exp.get("location"):
                line += f" [{exp['location']}]"
            if exp.get("bullets"):
                for bullet in exp["bullets"][:3]:
                    line += f"\n    • {bullet}"
            exp_lines.append(line)
        if exp_lines:
            parts.append("Experience:\n" + "\n".join(exp_lines))

    if candidate.skills:
        skill_parts = []
        if candidate.skills.get("programming_ml"):
            langs = [f"{s.get('language', '')} ({s.get('proficiency', '')})" for s in candidate.skills["programming_ml"]]
            skill_parts.append(f"Programming/ML: {', '.join(langs)}")
        if candidate.skills.get("domain_expertise"):
            skill_parts.append(f"Domain: {', '.join(candidate.skills['domain_expertise'])}")
        if candidate.skills.get("software_tools"):
            skill_parts.append(f"Tools: {', '.join(candidate.skills['software_tools'])}")
        if skill_parts:
            parts.append("Skills:\n" + "\n".join(f"  - {p}" for p in skill_parts))

    if candidate.profile_statement:
        parts.append(f"Profile: {candidate.profile_statement}")

    return "\n\n".join(parts) if parts else "Profile not yet completed."


def _build_job_summary_for_apply(job: JobPosting) -> str:
    """Build job summary for apply prompts."""
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
    if job.language:
        parts.append(f"Language: {job.language}")

    if job.description:
        desc = job.description[:1500] + ("..." if len(job.description) > 1500 else "")
        parts.append(f"Description:\n{desc}")

    if job.requirements:
        reqs = "\n".join(f"  • {r}" for r in job.requirements[:8])
        parts.append(f"Requirements:\n{reqs}")

    return "\n\n".join(parts)


# ── Company research ────────────────────────────────────────────────


async def _fetch_company_info(
    job: JobPosting,
    provider_config: dict | None = None,
) -> str | None:
    """Fetch basic company information for the reviewer.

    Uses a lightweight LLM call to generate company context from the
    job posting's company name. The output is a short summary of what
    the company does, recent news, and products — so the reviewer can
    verify cover letter claims.

    Falls back gracefully (returns None) if the LLM call fails or the
    company name is not meaningful.

    The prompt is kept small ("What is [Company] known for?" style)
    to minimize token usage — this is auxiliary context, not an analysis.
    """
    if not job.company or job.company in ("Not specified", "Unknown", ""):
        return None

    try:
        provider_kwargs = _get_provider_kwargs(provider_config)

        result = await llm_completion(
            messages=[
                {"role": "system", "content":
                 "You are a company research assistant. Given a company name, "
                 "provide a brief, factual summary (2-3 sentences) of what the "
                 "company does, its industry, known products/services, and any "
                 "recent news or growth signals. If you don't know specific details, "
                 "say so — don't fabricate."},
                {"role": "user", "content": f"What is {job.company} known for? Provide a short factual summary."},
            ],
            **provider_kwargs,
            temperature=0.0,
            max_tokens=300,
        )
        return result.strip() if result else None
    except Exception as e:
        logger.warning(f"Company research failed for {job.company}: {e}")
        return None


# ── PDF page count (no LaTeX dependency) ──────────────────────────────


def _get_pdf_page_count(pdf_path: Path) -> int:
    """Get page count of a PDF by parsing the /Pages /Count entry."""
    data = pdf_path.read_bytes()
    match = re.search(rb"/Type\s*/Pages[^/]*/Count\s*(\d+)", data)
    if match:
        return int(match.group(1))
    return 1


# ── Main orchestration ──────────────────────────────────────────────


async def execute_apply(
    db: AsyncSession,
    user_id: str,
    job_posting_id: str,
    rank_evaluation_id: str | None = None,
    provider_config: dict | None = None,
    application: Application | None = None,
) -> ApplyResult:
    """Execute the full apply workflow with Drafter-Reviewer pipeline.

    Pipeline stages:
    1. DRAFT: Generate tailored experience + cover letter (JSON schema)
    2. REVIEW: Second LLM call critiques the draft (fresh context)
    3. REVISE: Apply feedback and regenerate
    4. COMPILE: Typst compilation and page count verification

    The pipeline_stage is persisted in the Application record so the
    frontend can show real-time progress.

    If ``application`` is provided, the pipeline updates it in-place
    (used by background task). Otherwise a new Application is created.
    """
    with bind_context(pipeline_stage="apply", job_id=job_posting_id):

        # 1. Load all dependencies sequentially (async session does not support
        #    concurrent execute() on the same session — the greenlet-based
        #    provisioning would conflict).
        job_res = await db.execute(
            select(JobPosting).where(
                JobPosting.id == job_posting_id,
                JobPosting.user_id == user_id,
            )
        )
        job = job_res.scalar_one_or_none()
        if job is None:
            raise NotFoundError("Job posting not found.")

        if rank_evaluation_id:
            eval_res = await db.execute(
                select(RankEvaluation).where(
                    RankEvaluation.id == rank_evaluation_id,
                    RankEvaluation.job_posting_id == job_posting_id,
                )
            )
        else:
            eval_res = await db.execute(
                select(RankEvaluation)
                .where(RankEvaluation.job_posting_id == job_posting_id)
                .order_by(RankEvaluation.created_at.desc())
            )
        evaluation = eval_res.scalar_one_or_none()
        if evaluation is None:
            raise NotFoundError("Rank evaluation not found. Run /rank first.")

        cand_res = await db.execute(
            select(CandidateProfile)
            .options(selectinload(CandidateProfile.user))
            .where(CandidateProfile.user_id == user_id)
        )
        candidate = cand_res.scalar_one_or_none()
        if candidate is None:
            raise ProfileIncompleteError("Candidate profile not found. Run /setup first.")

        # ═══════════════════════════════════════════════════════════════
        # Create or initialize Application record for stage tracking
        # ═══════════════════════════════════════════════════════════════
        if application is None:
            application = Application(
                user_id=user_id,
                job_posting_id=job_posting_id,
                rank_evaluation_id=evaluation.id,
                language=job.language or "en",
                pipeline_stage="draft",
            )
            db.add(application)
            await db.commit()
            await db.refresh(application)
        else:
            application.rank_evaluation_id = evaluation.id
            application.language = job.language or "en"
            application.pipeline_stage = "draft"
            await db.commit()

        # ═══════════════════════════════════════════════════════════════
        # JSON/Typst pipeline
        # ═══════════════════════════════════════════════════════════════

        from app.services.apply_json import (
            generate_cv as _json_cv,
            generate_cover_letter as _json_cl,
            generate_review as _json_review,
            generate_revision as _json_revise,
        )
        from app.services.pdf_compiler_typst import compile_cv as _typst_compile

        # STAGE 1: DRAFT — JSON
        cv_output = await _json_cv(candidate, job, evaluation, provider_config)
        cv_cover = await _json_cl(candidate, job, evaluation, provider_config)
        if cv_cover is not None:
            cv_output.cv.cover_letter = cv_cover

        # STAGE 2: PERSIST DRAFT
        application.pipeline_stage = "draft"
        application.draft_cv_tex = json.dumps(
            cv_output.cv.model_dump(), indent=2, ensure_ascii=False
        )
        await db.commit()

        # STAGE 3: REVIEW (fresh context — reviewer sees JSON, not drafter reasoning)
        company_research = await _fetch_company_info(job, provider_config)
        cv_dict = cv_output.cv.model_dump()
        review_feedback = await _json_review(
            cv_dict, candidate, job, evaluation, provider_config,
        )

        application.pipeline_stage = "reviewed"
        application.review_feedback = review_feedback.model_dump()
        application.review_issues = [i.model_dump() for i in review_feedback.issues]
        await db.commit()

        # STAGE 4: REVISE
        cv_output = await _json_revise(
            cv_dict, review_feedback, candidate, job, provider_config,
        )

        application.pipeline_stage = "revised"
        await db.commit()

        # STAGE 5-6: RENDER + COMPILE via Typst
        final_cv_dict = cv_output.cv.model_dump()
        generated_dir = Path("generated") / user_id / job_posting_id
        generated_dir.mkdir(parents=True, exist_ok=True)
        cv_pdf_path = generated_dir / f"cv_{job.company}_{job.title}.pdf"
        _typst_compile(final_cv_dict, output=cv_pdf_path)
        cv_pages = _get_pdf_page_count(cv_pdf_path)
        cv_compiled = True

        if cv_output.cv.cover_letter is not None:
            # Cover letter is embedded in same PDF (after pagebreak)
            cover_pdf_path = cv_pdf_path
            cover_pages = max(0, cv_pages - 1)
            cover_compiled = True
        else:
            cover_pdf_path = None
            cover_pages = 0
            cover_compiled = False

        # Store JSON CV for audit
        application.tailored_experience = final_cv_dict.get("experience", [])
        application.incorporated_keywords = [
            kw.model_dump() for kw in (cv_output.metadata.incorporated_keywords or [])
        ]
        application.addressed_red_flags = [
            rf.model_dump() for rf in (cv_output.metadata.addressed_red_flags or [])
        ]

        # ATS check
        ats_result = None
        try:
            ats_result = await ats_check.check_ats_parseability(
                pdf_path=cv_pdf_path,
                job_posting=job,
                candidate=candidate,
            )
        except Exception as e:
            logger.warning(f"ATS check failed (non-blocking): {e}")

        application.cv_pdf_path = str(cv_pdf_path)
        application.cv_compiled = True
        application.cv_pages = cv_pages
        application.cover_letter_pdf_path = str(cover_pdf_path) if cover_pdf_path else None
        application.cover_letter_compiled = cover_compiled
        application.cover_letter_pages = cover_pages
        application.pipeline_stage = "verified" if (ats_result and ats_result.pass_ats) else "compiled"
        application.ats_score = ats_result.keyword_coverage if ats_result else None
        application.ats_missing_keywords = ats_result.missing_keywords if ats_result else None
        application.ats_pass = ats_result.pass_ats if ats_result else None
        application.ats_checked_at = datetime.now(timezone.utc) if ats_result else None

        job.status = "applied"
        await db.commit()
        await db.refresh(application)

        ats_summary = ""
        if ats_result is not None:
            ats_summary = (
                f" ATS check passed ({ats_result.keyword_coverage:.0%} keyword coverage)."
                if ats_result.pass_ats else
                f" ATS check flagged {len(ats_result.missing_keywords)} missing keywords."
            )

        return ApplyResult(
            application_id=application.id,
            cv_compiled=True,
            cv_pages=cv_pages,
            cover_letter_compiled=cover_compiled,
            cover_letter_pages=cover_pages,
            message=f"Application generated: "
                    f"CV ({cv_pages} pages), Cover Letter ({cover_pages} page)."
                    f"{ats_summary}",
        )


async def execute_apply_background(
    application_id: str,
    provider_config: dict | None = None,
):
    """Run the apply pipeline in the background, updating progress progressively.

    Creates its own DB session so it can outlive the HTTP request.
    On success the Application record contains the full generated output;
    on failure pipeline_stage is set to ``failed``.
    """
    session = async_session_factory()
    try:
        logger.info("execute_apply_background: loading application %s", application_id)
        application = await session.get(Application, application_id)
        if application is None:
            logger.error("execute_apply_background: Application %s not found", application_id)
            return

        application.pipeline_stage = "initializing"
        await session.commit()
        logger.info("execute_apply_background: stage=initializing committed, calling execute_apply")

        result = await execute_apply(
            db=session,
            user_id=application.user_id,
            job_posting_id=application.job_posting_id,
            provider_config=provider_config,
            application=application,
        )
        logger.info("execute_apply_background: execute_apply completed successfully (stage=%s)", application.pipeline_stage)
    except asyncio.CancelledError:
        logger.warning("Pipeline cancelled for application %s", application_id)
        await _fail_application(session, application_id)
        raise
    except BaseException as e:
        logger.error("Pipeline failed for application %s: %s", application_id, e, exc_info=True)
        await _fail_application(session, application_id)
    finally:
        try:
            await session.close()
        except Exception:
            pass


async def _fail_application(session: AsyncSession, application_id: str) -> None:
    """Set application pipeline_stage to 'failed'. Errors are swallowed."""
    try:
        try:
            await session.rollback()
        except Exception:
            pass
        app = await session.get(Application, application_id)
        if app is not None:
            app.pipeline_stage = "failed"
            await session.commit()
    except Exception:
        pass


def _get_provider_kwargs(provider_config: dict | None) -> dict:
    """Extract provider kwargs from provider config for LLM calls."""
    if not provider_config:
        return {}

    return {
        "provider": provider_config.get("provider"),
        "model": provider_config.get("model"),
        "api_key": provider_config.get("api_key"),
        "api_base": provider_config.get("api_base"),
    }


def _extract_incorporated_keywords(
    tailored_experience: list[TailoredExperienceEntry],
    missing_keywords: list[str],
) -> list[IncorporatedKeyword]:
    """Extract which missing keywords were incorporated and where."""
    incorporated = []
    all_text = " ".join(
        " ".join(exp.bullets) for exp in tailored_experience
    ).lower()

    for keyword in missing_keywords:
        if keyword.lower() in all_text:
            where = "experience section"
            for exp in tailored_experience:
                for i, bullet in enumerate(exp.bullets):
                    if keyword.lower() in bullet.lower():
                        where = f"{exp.title} at {exp.company}, bullet {i+1}"
                        break

            incorporated.append(
                IncorporatedKeyword(
                    keyword=keyword,
                    where_incorporated=where,
                    original_context=f"Required in job posting: {keyword}",
                )
            )

    return incorporated


def _extract_addressed_red_flags(
    tailored_experience: list[TailoredExperienceEntry],
    red_flags: list[str],
) -> list[AddressedRedFlag]:
    """Extract which red flags were addressed."""
    addressed = []
    all_text = " ".join(
        " ".join(exp.bullets) for exp in tailored_experience
    ).lower()

    for flag in red_flags:
        flag_keywords = flag.lower().split()
        if any(kw in all_text for kw in flag_keywords if len(kw) > 3):
            addressed.append(
                AddressedRedFlag(
                    red_flag=flag,
                    how_addressed="Reframed in tailored experience bullets",
                )
            )

    return addressed


# ── Query helpers ───────────────────────────────────────────────────


async def get_application(
    db: AsyncSession, application_id: str, user_id: str
) -> Application:
    """Get an application by ID, verifying ownership."""
    result = await db.execute(
        select(Application)
        .where(Application.id == application_id)
        .where(Application.user_id == user_id)
    )
    application = result.scalar_one_or_none()
    if application is None:
        raise NotFoundError("Application not found.")
    return application


async def list_applications(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[Application]:
    """List applications for a user."""
    result = await db.execute(
        select(Application)
        .where(Application.user_id == user_id)
        .order_by(Application.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
