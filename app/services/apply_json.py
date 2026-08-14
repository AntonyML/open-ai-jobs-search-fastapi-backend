"""JSON-path prompt builders and generation functions for the apply pipeline.

Used when ``use_typst=True``.  Generates ``GenerateCVOutput`` via the LLM,
sanitizes with ``LLMResponseSanitizer``, and renders via Typst.

The reviewer receives the JSON CV (serialised) + profile + job posting only
— never the drafter's reasoning, maintaining adversarial freshness.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.core.logging import bind_context, get_logger
from app.db.models import CandidateProfile, JobPosting, RankEvaluation
from app.exceptions import LLMError
from app.llm.adapter import llm_completion
from app.schemas.apply import ReviewFeedback
from app.schemas.cv import CV, CoverLetter, CVAnalysis, CVMetadata, GenerateCVOutput
from app.services.orchestrator.llm_response_sanitizer import (
    default_field_constraints,
    sanitize_llm_response,
)

logger = get_logger(__name__)

# ── Guardrails (reused from existing prompts) ────────────────────────

APPLY_GUARDRAIL = """
IMPORTANT GUARDRAIL: You are tailoring a candidate's CV and cover letter for a specific job.
You MUST NEVER invent, hallucinate, or assume experience, titles, companies, or skills
that the candidate does not explicitly have in their profile.

Your role is to:
- REFRAME existing experience using the X-Y-Z formula
- Incorporate missing keywords ONLY where they are genuinely true
- Address red flags by honestly reframing, not by hiding or inventing
- Keep all claims defensible in an interview
"""

XYZ_GUIDANCE = """
Use the X-Y-Z formula for every experience bullet:
- X = What you accomplished
- Y = How it was measured
- Z = How you did it
"""

REVIEWER_GUARDRAIL = """
You are a CRITICAL REVIEWER evaluating a draft CV.
Rules:
- Compare EVERY bullet against the candidate's real profile (ground truth)
- Check that missing keywords from the job posting are incorporated where genuinely true
- Flag any generic/filler bullets that lack the X-Y-Z formula
- Verify profile statement mentions the target role title
- Check for invented claims not supported by the candidate's profile
- Identify weak framing that could be stronger
Output structured JSON with ReviewFeedback schema.
"""

REVISE_GUARDRAIL = """
You are a SKILLED EDITOR applying reviewer feedback.
Rules:
- Only change what was flagged — do not arbitrarily rewrite
- NEVER fabricate experience or skills
- Preserve the X-Y-Z formula in all bullets
- Maintain consistent tone throughout
- Incorporate missing keywords where genuinely true
"""


# ── Prompt builders ──────────────────────────────────────────────────


def _build_candidate_summary(candidate: CandidateProfile) -> str:
    """Plain-text candidate summary for prompt context."""
    parts = []
    if candidate.location:
        parts.append(f"Location: {candidate.location}")
    if candidate.phone:
        parts.append(f"Phone: {candidate.phone}")
    if candidate.linkedin_url:
        parts.append(f"LinkedIn: {candidate.linkedin_url}")
    if candidate.github_url:
        parts.append(f"GitHub: {candidate.github_url}")

    # Identity via User relationship
    try:
        full_name = candidate.full_name
        email = candidate.email
        if full_name:
            parts.insert(0, f"Name: {full_name}")
        if email:
            parts.append(f"Email: {email}")
    except Exception:
        pass

    if candidate.profile_statement:
        parts.append(f"Profile:\n{candidate.profile_statement}")

    if candidate.education:
        parts.append("\nEducation:")
        for e in candidate.education[:2]:
            line = f"  - {e.get('degree', '')} at {e.get('institution', '')}"
            if e.get("period"):
                line += f" ({e['period']})"
            parts.append(line)

    if candidate.experience:
        parts.append("\nExperience:")
        for e in candidate.experience[:3]:
            line = f"  - {e.get('title', '')} at {e.get('company', '')}"
            if e.get("start_date") or e.get("end_date"):
                line += f" ({e.get('start_date', '')}–{e.get('end_date', '')})"
            parts.append(line)
            for b in e.get("bullets", [])[:2]:
                parts.append(f"      • {b}")

    if candidate.skills:
        parts.append("\nSkills:")
        skills = candidate.skills
        if skills.get("programming_ml"):
            parts.append("  Programming/ML: " + ", ".join(
                f"{s.get('language', '')} ({s.get('proficiency', '')})"
                for s in skills["programming_ml"][:5]
            ))
        if skills.get("domain_expertise"):
            parts.append("  Domain: " + ", ".join(skills["domain_expertise"][:5]))
        if skills.get("software_tools"):
            parts.append("  Tools: " + ", ".join(skills["software_tools"][:5]))

    if candidate.languages:
        parts.append("  Languages: " + ", ".join(
            f"{l.get('language', '')} ({l.get('proficiency', '')})"
            for l in candidate.languages[:3]
        ))

    return "\n".join(parts)


def _build_job_summary(job: JobPosting) -> str:
    """Plain-text job posting summary."""
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company or 'Not specified'}",
        f"Location: {job.location or 'Not specified'}",
    ]
    if job.description:
        parts.append(f"\nDescription:\n{job.description[:2000]}")
    if job.requirements:
        parts.append("\nRequirements:\n" + "\n".join(f"  • {r}" for r in job.requirements[:10]))
    if job.salary:
        parts.append(f"Salary: {job.salary}")
    return "\n".join(parts)


def _build_rank_insights(evaluation: RankEvaluation | None) -> str:
    """Extract red flags and missing keywords from the rank evaluation."""
    if evaluation is None:
        return ""
    parts = []
    if evaluation.red_flags:
        parts.append("Red flags to address:\n" + "\n".join(f"  • {f}" for f in evaluation.red_flags[:5]))
    if evaluation.missing_keywords:
        parts.append("Missing keywords to incorporate:\n" + "\n".join(f"  • {kw}" for kw in evaluation.missing_keywords[:10]))
    if evaluation.overall_score is not None:
        parts.append(f"Overall fit score: {evaluation.overall_score}/100")
    return "\n".join(parts)


def build_json_drafter_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None = None,
) -> list[dict[str, str]]:
    """Build the drafter prompt that outputs ``GenerateCVOutput``."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)
    rank_insights = _build_rank_insights(evaluation)

    system = (
        "You are an expert CV writer. Generate a complete, tailored CV document "
        "in JSON format according to the provided schema.\n\n"
        + APPLY_GUARDRAIL + "\n" + XYZ_GUIDANCE + "\n\n"
        + "Your output MUST have the following structure:\n"
        + json.dumps(GenerateCVOutput.model_json_schema(), indent=2)
    )

    user = f"""
Generate a tailored CV for the following candidate applying to the job below.

=== CANDIDATE PROFILE ===
{candidate_summary}

=== JOB POSTING ===
{job_summary}

{("=== RANK EVALUATION INSIGHTS ===\n" + rank_insights) if rank_insights else ""}

=== INSTRUCTIONS ===
1. Tailor the experience bullets using X-Y-Z formula
2. Incorporate job keywords where genuinely supported by the profile
3. Address red flags by honest reframing
4. Generate a compelling profile statement
5. Choose skill group labels appropriate to the profession
6. Set the cv.language field to match the job posting language
7. If you can generate a strong cover letter, include it; otherwise omit it

Output a valid GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_json_cover_letter_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None = None,
) -> list[dict[str, str]]:
    """Build the cover letter prompt (separate call, uses generated CV context)."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    system = (
        "You are an expert cover letter writer. Generate a tailored cover letter "
        "in JSON format.\n\n" + APPLY_GUARDRAIL + "\n\n"
        "Your output must follow this schema:\n"
        + json.dumps(CoverLetter.model_json_schema(), indent=2)
    )

    user = f"""
Generate a cover letter for the following candidate applying to this job.

=== CANDIDATE PROFILE ===
{candidate_summary}

=== JOB POSTING ===
{job_summary}

=== INSTRUCTIONS ===
1. Opening paragraph: hook + mention the role
2. Body paragraphs (2-4): highlight relevant achievements and fit
3. Company connection paragraph: show you researched the company
4. Closing: call to action + thank you
5. Write in the same language as the job posting
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_json_review_prompt(
    cv_json: dict[str, Any],
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None = None,
) -> list[dict[str, str]]:
    """Build the reviewer prompt.

    The reviewer receives ONLY the JSON CV + profile + job posting.
    It does NOT see the drafter's reasoning or self-evaluation.
    This maintains adversarial freshness: the reviewer evaluates the output
    independently, not the intent behind it.
    """
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    system = REVIEWER_GUARDRAIL + "\n\n" + (
        "Your output must follow the ReviewFeedback schema:\n"
        + json.dumps(ReviewFeedback.model_json_schema(), indent=2)
    )

    # Serialise the CV JSON (the reviewer reads the actual generated content)
    cv_text = json.dumps(cv_json, indent=2, ensure_ascii=False)

    user = f"""
Review the following CV JSON against the candidate profile and job posting.
Be critical — this is the quality gate.

=== CV JSON TO REVIEW ===
{cv_text}

=== CANDIDATE PROFILE (ground truth) ===
{candidate_summary}

=== JOB POSTING ===
{job_summary}

{("=== MISSING KEYWORDS (from rank evaluation) ===\n" + "\n".join(f"  • {kw}" for kw in (evaluation.missing_keywords or []))) if evaluation and evaluation.missing_keywords else ""}

{("=== RED FLAGS ===\n" + "\n".join(f"  • {f}" for f in (evaluation.red_flags or []))) if evaluation and evaluation.red_flags else ""}

=== EVALUATION CRITERIA ===
1. Does each bullet use X-Y-Z formula? Flag generic bullets.
2. Are any claims NOT supported by the candidate profile? Flag as fabricated.
3. Are missing keywords incorporated where genuinely defensible?
4. Is the profile statement compelling and role-specific?
5. Is the skill grouping appropriate for the profession?
6. Are red flags addressed by honest reframing?
7. Is the cover letter (if present) tailored to the company?

Output a ReviewFeedback JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_json_revise_prompt(
    old_cv: dict[str, Any],
    review_feedback: ReviewFeedback,
    candidate: CandidateProfile,
    job: JobPosting,
) -> list[dict[str, str]]:
    """Build the revise prompt — applies reviewer feedback to the CV JSON."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    system = REVISE_GUARDRAIL + "\n\n" + (
        "Your output must be a valid GenerateCVOutput JSON object:\n"
        + json.dumps(GenerateCVOutput.model_json_schema(), indent=2)
    )

    cv_text = json.dumps(old_cv, indent=2, ensure_ascii=False)
    review_text = review_feedback.model_dump_json(indent=2)

    user = f"""
Revise the following CV JSON based on the reviewer's feedback.

=== CURRENT CV JSON ===
{cv_text}

=== REVIEWER FEEDBACK ===
{review_text}

=== CANDIDATE PROFILE ===
{candidate_summary}

=== JOB POSTING ===
{job_summary}

=== INSTRUCTIONS ===
Address all issues flagged by the reviewer. Only change what was critiqued.
Output the revised CV as a valid GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


# ── CV generator prompts (FASE 1 — no JobPosting required) ────────────


def build_base_cv_prompt(candidate: CandidateProfile) -> list[dict[str, str]]:
    """Build the prompt for a generic base CV (no job context).

    Used by ``POST /cv/base``.  The output is ``GenerateCVOutput`` without any
    job-tailoring, so it can later be personalized via ``POST /cv/personalize``.
    """
    candidate_summary = _build_candidate_summary(candidate)

    system = (
        "You are an expert CV writer. Generate a complete, polished CV document "
        "in JSON format according to the provided schema.\n\n"
        + APPLY_GUARDRAIL + "\n" + XYZ_GUIDANCE + "\n\n"
        + "Your output MUST have the following structure:\n"
        + json.dumps(GenerateCVOutput.model_json_schema(), indent=2)
    )

    user = f"""
Generate a generic base CV for the following candidate.

=== CANDIDATE PROFILE ===
{candidate_summary}

=== INSTRUCTIONS ===
1. Present experience bullets using the X-Y-Z formula
2. Choose skill group labels appropriate to the profession
3. Generate a compelling profile statement
4. Keep the CV generic but polished — it may be tailored to specific jobs later
5. Set the cv.language field to the candidate's primary language when determinable, otherwise 'en'
6. If you can generate a strong generic cover letter, include it; otherwise omit it

Output a valid GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_recruiter_analysis_prompt(
    candidate: CandidateProfile,
    job_description_text: str,
) -> list[dict[str, str]]:
    """Build the recruiter-lens analysis prompt for ``CVAnalysis``.

    Run BEFORE drafting so the drafter can inject missing keywords and preempt
    red flags.  Only free-text job description is required — no scraping.
    """
    candidate_summary = _build_candidate_summary(candidate)

    system = (
        "You are a technical recruiter analyzing a candidate's profile against a "
        "job description. Your output must follow this schema:\n"
        + json.dumps(CVAnalysis.model_json_schema(), indent=2)
    )

    user = f"""
Analyze how well the candidate matches the job description below.

=== CANDIDATE PROFILE ===
{candidate_summary}

=== JOB DESCRIPTION ===
{job_description_text}

=== INSTRUCTIONS ===
1. match_score: estimate a 0–100 score for overall fit
2. missing_keywords: keywords in the job the candidate should emphasize — ONLY those genuinely supported by the profile
3. red_flags: potential concerns a recruiter could raise (max 5)
4. adapted_experience: concrete reframing suggestions the drafter should apply

Output a valid CVAnalysis JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_personalize_drafter_prompt(
    candidate: CandidateProfile,
    job_description_text: str,
    analysis: CVAnalysis,
) -> list[dict[str, str]]:
    """Build the personalize drafter prompt — tailors a CV to free-text job text."""
    candidate_summary = _build_candidate_summary(candidate)

    system = (
        "You are an expert CV writer. Generate a complete, tailored CV document "
        "in JSON format according to the provided schema.\n\n"
        + APPLY_GUARDRAIL + "\n" + XYZ_GUIDANCE + "\n\n"
        + "Your output MUST have the following structure:\n"
        + json.dumps(GenerateCVOutput.model_json_schema(), indent=2)
    )

    user = f"""
Tailor the candidate's CV to the job description below.

=== CANDIDATE PROFILE ===
{candidate_summary}

=== JOB DESCRIPTION ===
{job_description_text}

=== RECRUITER ANALYSIS ===
{analysis.model_dump_json(indent=2)}

=== INSTRUCTIONS ===
1. Tailor the experience bullets using the X-Y-Z formula
2. Incorporate the missing keywords ONLY where genuinely supported by the profile
3. Address the red flags by honest reframing
4. Apply the adapted_experience suggestions where defensible
5. Generate a compelling, role-specific profile statement
6. Choose skill group labels appropriate to the profession
7. Set the cv.language field to match the job description language
8. If you can generate a strong cover letter, include it; otherwise omit it

Output a valid GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


# ── LLM generation functions (with sanitizer) ────────────────────────


async def _llm_json(
    messages: list[dict[str, str]],
    schema_type: type,
    provider_config: dict[str, Any],
    temperature: float = 0.3,
    max_tokens: int = 4000,
    field_constraints: dict | None = None,
) -> dict[str, Any]:
    """Call LLM, sanitize response, validate against Pydantic schema.

    Uses ``llm_completion()`` (no response_format) + ``sanitize_llm_response()``
    so the JSON repair pipeline handles malformed LLM output before Pydantic.
    """
    provider = provider_config.get("provider", "anthropic")
    model = provider_config.get("model", "claude-sonnet-4-20250514")
    api_key = provider_config.get("api_key")

    raw = await llm_completion(
        messages=messages,
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    constraints = field_constraints or default_field_constraints()
    try:
        cleaned = sanitize_llm_response(raw, schema_type.__name__, constraints)
    except ValueError as first_exc:
        # Retry once: large schemas often get truncated (max_tokens) or wrapped
        # in prose. Ask for the complete JSON only, with a larger token budget.
        logger.warning(
            "LLM response unparseable for %s — retrying once. %s",
            schema_type.__name__, str(first_exc)[:160],
        )
        retry_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "Your previous response was not valid JSON. "
                    "Output ONLY the complete JSON object matching the schema — "
                    "no explanations, no markdown, and do NOT truncate it."
                ),
            },
        ]
        raw = await llm_completion(
            messages=retry_messages,
            provider=provider,
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max(max_tokens, 8000),
        )
        try:
            cleaned = sanitize_llm_response(raw, schema_type.__name__, constraints)
        except ValueError as exc:
            raise LLMError(
                f"LLM response could not be parsed for {schema_type.__name__} (after retry): {exc}"
            ) from exc

    try:
        schema_type.model_validate(cleaned)
        return cleaned
    except ValidationError as exc:
        raise LLMError(
            f"LLM response failed {schema_type.__name__} validation after sanitization: {exc}"
        )


async def generate_cv(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None = None,
    provider_config: dict[str, Any] | None = None,
    temperature: float = 0.3,
) -> GenerateCVOutput:
    """Generate a full CV (without cover letter) as ``GenerateCVOutput``."""
    provider_config = provider_config or {}
    messages = build_json_drafter_prompt(candidate, job, evaluation)
    raw_dict = await _llm_json(
        messages, GenerateCVOutput, provider_config,
        temperature=temperature, max_tokens=8000,
    )
    return GenerateCVOutput(**raw_dict)


async def generate_cover_letter(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None = None,
    provider_config: dict[str, Any] | None = None,
) -> CoverLetter | None:
    """Generate a cover letter as a ``CoverLetter`` object."""
    provider_config = provider_config or {}
    messages = build_json_cover_letter_prompt(candidate, job, evaluation)
    try:
        raw_dict = await _llm_json(
            messages, CoverLetter, provider_config,
            temperature=0.4, max_tokens=2000,
        )
        return CoverLetter(**raw_dict)
    except LLMError as exc:
        logger.warning("Cover letter generation failed (non-fatal): %s", exc)
        return None


async def generate_review(
    cv_json: dict[str, Any],
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None = None,
    provider_config: dict[str, Any] | None = None,
) -> ReviewFeedback:
    """Review the generated CV JSON (fresh context, no drafter reasoning)."""
    provider_config = provider_config or {}
    messages = build_json_review_prompt(cv_json, candidate, job, evaluation)
    raw_dict = await _llm_json(
        messages, ReviewFeedback, provider_config,
        temperature=0.0, max_tokens=4000,
    )
    return ReviewFeedback(**raw_dict)


async def generate_revision(
    old_cv: dict[str, Any],
    review_feedback: ReviewFeedback,
    candidate: CandidateProfile,
    job: JobPosting,
    provider_config: dict[str, Any] | None = None,
    temperature: float = 0.2,
) -> GenerateCVOutput:
    """Revise the CV JSON based on reviewer feedback."""
    provider_config = provider_config or {}
    messages = build_json_revise_prompt(
        old_cv, review_feedback, candidate, job,
    )
    raw_dict = await _llm_json(
        messages, GenerateCVOutput, provider_config,
        temperature=temperature, max_tokens=8000,
    )
    return GenerateCVOutput(**raw_dict)


# ── CV generator LLM functions (FASE 1) ──────────────────────────────


async def generate_base_cv_llm(
    candidate: CandidateProfile,
    provider_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a generic base CV (``GenerateCVOutput``) with no job context."""
    provider_config = provider_config or {}
    messages = build_base_cv_prompt(candidate)
    raw_dict = await _llm_json(
        messages, GenerateCVOutput, provider_config,
        temperature=0.3, max_tokens=8000,
    )
    return raw_dict


async def personalize_cv_llm(
    candidate: CandidateProfile,
    job_description_text: str,
    provider_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the two-step personalize pipeline for free-text job descriptions.

    Returns ``(analysis_dict, generate_output_dict)``.  The analysis is a
    ``CVAnalysis``-shaped dict (match score, missing keywords, red flags,
    adapted experience); the second element is the tailored ``GenerateCVOutput``.
    """
    provider_config = provider_config or {}

    analysis_dict = await _llm_json(
        build_recruiter_analysis_prompt(candidate, job_description_text),
        CVAnalysis,
        provider_config,
        temperature=0.2,
        max_tokens=2000,
    )
    analysis = CVAnalysis(**analysis_dict)

    output_dict = await _llm_json(
        build_personalize_drafter_prompt(candidate, job_description_text, analysis),
        GenerateCVOutput,
        provider_config,
        temperature=0.3,
        max_tokens=8000,
    )
    return analysis_dict, output_dict


# ── CV adapter LLM functions (FASE — Perfil → CV base → CV adaptado) ────


@dataclass
class AdaptJobContext:
    """Minimal job context for the adapt prompts when there is no stored
    ``JobPosting`` row — e.g. a job fetched live from a public URL.

    Mirrors the ``JobPosting`` attributes ``_build_adapt_job_summary`` reads,
    so both the internal-offer flow and the by-URL flow share one prompt path.
    """

    title: str
    description: str
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    salary: str | None = None
    language: str | None = None
    requirements: list[str] | None = field(default_factory=list)


JobContext = JobPosting | AdaptJobContext


def _build_adapt_job_summary(job: JobContext) -> str:
    """Plain-text summary of a stored JobPosting for adaptation context."""
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company or 'Not specified'}",
        f"Location: {job.location or 'Not specified'}",
    ]
    if job.employment_type:
        parts.append(f"Employment type: {job.employment_type}")
    if job.salary:
        parts.append(f"Salary: {job.salary}")
    if job.language:
        parts.append(f"Posting language: {job.language}")
    if job.description:
        parts.append(f"\nDescription:\n{job.description[:4000]}")
    if job.requirements:
        parts.append(
            "\nRequirements:\n" + "\n".join(f"  • {r}" for r in job.requirements[:15])
        )
    return "\n".join(parts)


def build_adapt_analysis_prompt(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    job: JobContext,
) -> list[dict[str, str]]:
    """Recruiter-lens analysis using the base CV as the candidate representation."""
    candidate_summary = _build_candidate_summary(candidate)
    base_cv_text = json.dumps(base_cv_json, indent=2, ensure_ascii=False)
    job_summary = _build_adapt_job_summary(job)

    system = (
        "You are a technical recruiter analyzing a candidate's base CV against a "
        "job posting. Your output must follow this schema:\n"
        + json.dumps(CVAnalysis.model_json_schema(), indent=2)
    )

    user = f"""
Analyze how well the candidate matches the job posting below.

=== CANDIDATE BASE CV ===
{base_cv_text[:6000]}

=== CANDIDATE PROFILE ===
{candidate_summary}

=== JOB POSTING ===
{job_summary}

=== INSTRUCTIONS ===
1. match_score: estimate a 0–100 score for overall fit
2. missing_keywords: keywords in the job the candidate should emphasize — ONLY those genuinely supported by the profile
3. red_flags: potential concerns a recruiter could raise (max 5)
4. adapted_experience: concrete reframing suggestions the drafter should apply

Output a valid CVAnalysis JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_adapt_drafter_prompt(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    job: JobContext,
    analysis: CVAnalysis,
) -> list[dict[str, str]]:
    """Drafter prompt — adapts the base CV to the job posting (never invents)."""
    base_cv_text = json.dumps(base_cv_json, indent=2, ensure_ascii=False)
    job_summary = _build_adapt_job_summary(job)
    candidate_summary = _build_candidate_summary(candidate)

    system = (
        "You are an expert CV writer. Adapt a candidate's BASE CV to a specific "
        "job posting, outputting a new tailored CV document in JSON format.\n\n"
        + APPLY_GUARDRAIL + "\n" + XYZ_GUIDANCE + "\n\n"
        + "Your output MUST have the following structure:\n"
        + json.dumps(GenerateCVOutput.model_json_schema(), indent=2)
    )

    user = f"""
Adapt the candidate's base CV to the job posting below.

=== BASE CV (STARTING POINT — never invent beyond it) ===
{base_cv_text[:6000]}

=== CANDIDATE PROFILE (ground truth) ===
{candidate_summary}

=== JOB POSTING ===
{job_summary}

=== RECRUITER ANALYSIS ===
{analysis.model_dump_json(indent=2)}

=== INSTRUCTIONS ===
1. Keep the candidate's real experience from the base CV — reframe bullets using the X-Y-Z formula
2. Incorporate the missing keywords ONLY where genuinely supported by the profile
3. Address the red flags by honest reframing
4. Apply the adapted_experience suggestions where defensible
5. Generate a compelling, role-specific profile statement
6. Choose skill group labels appropriate to the profession
7. Set the cv.language field to match the job posting language
8. If you can generate a strong cover letter, include it; otherwise omit it

Output a valid GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


async def adapt_cv_llm(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    job: JobContext,
    provider_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the two-step adapt pipeline: recruiter analysis → drafter.

    Returns ``(analysis_dict, output_dict)``.  The base CV JSON is only used
    as context; the generated ``GenerateCVOutput`` becomes a NEW document.
    """
    provider_config = provider_config or {}

    analysis_dict = await _llm_json(
        build_adapt_analysis_prompt(candidate, base_cv_json, job),
        CVAnalysis,
        provider_config,
        temperature=0.2,
        max_tokens=2000,
    )
    analysis = CVAnalysis(**analysis_dict)

    output_dict = await _llm_json(
        build_adapt_drafter_prompt(candidate, base_cv_json, job, analysis),
        GenerateCVOutput,
        provider_config,
        temperature=0.3,
        max_tokens=8000,
    )
    return analysis_dict, output_dict
