"""JSON-path prompt builders and generation functions for the apply pipeline.

Used when ``use_typst=True``.  Generates ``GenerateCVOutput`` via the LLM,
sanitizes with ``LLMResponseSanitizer``, and renders via Typst.

The reviewer receives the JSON CV (serialised) + profile + job posting only
— never the drafter's reasoning, maintaining adversarial freshness.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.core.logging import get_logger
from app.db.models import CandidateProfile, JobPosting, RankEvaluation
from app.exceptions import LLMError, WebSearchUnavailableError
from app.llm.adapter import (
    has_web_search_support,
    llm_completion,
    llm_completion_with_web_search,
)
from app.schemas.apply import ReviewFeedback
from app.schemas.cv import CoverLetter, CVAnalysis, GenerateCVOutput
from app.services.cv_linter import lint_cv
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

ATS_GUARDRAIL = """
ATS FORMATTING & FIDELITY RULES (follow strictly):
- All text fields must be plain text — no markdown (no **, no *, no #, no _)
- Email, phone, and links must appear as literal characters in the header, not as icons
- Skills must be plain text grouped under relevant domain categories reflecting the candidate's profession (e.g. Technical/Specialty, Tools/Software, Professional/Operational Competencies)
- No section labels with symbols (✓, →, •) — plain labels only
- Every experience bullet must open with a strong past-tense action verb
- Never use "Responsible for", "Helped with", "Assisted in", "Worked on" as openers
- NEVER invent new experience bullets, metrics, or technologies/competencies not present in the candidate profile
- Format existing points into high-impact bullets (Action + Context + Result) without inventing quantitative metrics
- Include candidate's certifications, languages, education, and links exactly as provided in the profile
- UNIVERSAL PROFILE PRINCIPLE: The candidate can be from any profession (gastronomy, healthcare, trades, administration, management, education, engineering, software, etc.). The absence of optional sections (such as GitHub, projects, certifications or software tools) is NEVER a deficiency. Select and highlight evidence solely from the real data provided.
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
    """Plain-text candidate summary for prompt context with zero arbitrary truncation."""
    parts = []
    # Identity via User relationship
    try:
        full_name = candidate.full_name
        email = candidate.email
        if full_name:
            parts.append(f"Name: {full_name}")
        if email:
            parts.append(f"Email: {email}")
    except Exception:
        pass

    if candidate.location:
        parts.append(f"Location: {candidate.location}")
    if candidate.phone:
        parts.append(f"Phone: {candidate.phone}")
    if candidate.linkedin_url:
        parts.append(f"LinkedIn: {candidate.linkedin_url}")
    if candidate.github_url:
        parts.append(f"GitHub: {candidate.github_url}")
    if getattr(candidate, "portfolio_url", None):
        parts.append(f"Portfolio/Website: {candidate.portfolio_url}")

    if candidate.profile_statement:
        parts.append(f"Profile Statement:\n{candidate.profile_statement}")

    if candidate.education:
        parts.append("\nEducation:")
        for e in candidate.education:
            deg = e.get("degree", "")
            inst = e.get("institution", "")
            dates = ""
            if e.get("start_date") or e.get("end_date"):
                dates = f" ({e.get('start_date', '')}–{e.get('end_date', '')})"
            elif e.get("period"):
                dates = f" ({e['period']})"
            line = f"  - {deg} at {inst}{dates}"
            if e.get("key_topics"):
                line += f" | Topics: {e['key_topics']}"
            parts.append(line)

    if candidate.experience:
        parts.append("\nExperience:")
        for e in candidate.experience:
            title = e.get("title", "")
            company = e.get("company", "")
            dates = ""
            if e.get("start_date") or e.get("end_date"):
                dates = f" ({e.get('start_date', '')}–{e.get('end_date', '')})"
            loc = f" — {e.get('location')}" if e.get("location") else ""
            ctx = f" [{e.get('client_context')}]" if e.get("client_context") else ""
            parts.append(f"  - {title} at {company}{loc}{ctx}{dates}")
            if e.get("technologies"):
                tech_str = ", ".join(e["technologies"]) if isinstance(e["technologies"], list) else str(e["technologies"])
                parts.append(f"    Technologies: {tech_str}")
            for b in e.get("bullets", []):
                parts.append(f"      • {b}")

    if getattr(candidate, "certifications", None) and candidate.certifications:
        parts.append("\nCertifications:")
        for c in candidate.certifications:
            name = c.get("name", "")
            issuer = c.get("issuer", "")
            year = c.get("issue_date") or c.get("year", "")
            url = c.get("credential_url") or c.get("url", "")
            c_line = f"  - {name} ({issuer})"
            if year:
                c_line += f" - {year}"
            if url:
                c_line += f" | Verify: {url}"
            parts.append(c_line)

    if candidate.projects:
        parts.append("\nHighlighted Projects & Works:")
        for p in candidate.projects:
            name = p.get("name", "")
            desc = p.get("description", "")
            role_str = f" ({p.get('role')})" if p.get("role") else ""
            client_str = f" at/for {p.get('client')}" if p.get("client") else ""
            dates = ""
            if p.get("start_date") or p.get("end_date"):
                dates = f" [{p.get('start_date', '')}–{p.get('end_date', '')}]"
            techs = p.get("technologies", [])
            tech_str = f" | Tools/Skills: {', '.join(techs)}" if techs else ""
            p_url = f" | Link: {p.get('url')}" if p.get("url") else ""
            parts.append(f"  - {name}{role_str}{client_str}{dates}: {desc}{tech_str}{p_url}")

    if candidate.skills:
        parts.append("\nSkills & Competencies:")
        skills = candidate.skills
        if isinstance(skills, dict):
            # Universal categorization
            if skills.get("programming_ml"):
                parts.append(
                    "  Languages/Technical: "
                    + ", ".join(
                        f"{s.get('language', s) if isinstance(s, dict) else s}" + (f" ({s.get('proficiency', '')})" if isinstance(s, dict) and s.get("proficiency") else "")
                        for s in skills["programming_ml"]
                    )
                )
            if skills.get("domain_expertise"):
                parts.append("  Specialty/Domain: " + ", ".join(skills["domain_expertise"]))
            if skills.get("databases"):
                parts.append("  Databases & Storage: " + ", ".join(skills["databases"]))
            if skills.get("architecture"):
                parts.append("  Architecture & Patterns: " + ", ".join(skills["architecture"]))
            if skills.get("software_tools"):
                parts.append("  Tools & Software: " + ", ".join(skills["software_tools"]))
            if skills.get("methodologies"):
                parts.append("  Professional & Methodological Competencies: " + ", ".join(skills["methodologies"]))
            if skills.get("custom_skills"):
                parts.append("  General Skills: " + ", ".join(skills["custom_skills"]))
        elif isinstance(skills, list):
            parts.append("  " + ", ".join(str(s) for s in skills))

    if candidate.languages:
        parts.append("\nLanguages:")
        for lang in candidate.languages:
            parts.append(f"  - {lang.get('language', '')}: {lang.get('proficiency', '')}")

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
        parts.append(
            "Missing keywords to incorporate:\n" + "\n".join(f"  • {kw}" for kw in evaluation.missing_keywords[:10])
        )
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
        + APPLY_GUARDRAIL
        + "\n"
        + XYZ_GUIDANCE
        + "\n\n"
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
        "Your output must follow this schema:\n" + json.dumps(CoverLetter.model_json_schema(), indent=2)
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

    system = (
        REVIEWER_GUARDRAIL
        + "\n\n"
        + (
            "Your output must follow the ReviewFeedback schema:\n"
            + json.dumps(ReviewFeedback.model_json_schema(), indent=2)
        )
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
"""  # noqa: E501
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

    system = (
        REVISE_GUARDRAIL
        + "\n\n"
        + (
            "Your output must be a valid GenerateCVOutput JSON object:\n"
            + json.dumps(GenerateCVOutput.model_json_schema(), indent=2)
        )
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


def build_base_cv_prompt(candidate: CandidateProfile, language: str = "es") -> list[dict[str, str]]:
    """Build the prompt for a generic base CV (no job context).

    Used by ``POST /cv/base``.  The output is ``GenerateCVOutput`` without any
    job-tailoring, so it can later be personalized via ``POST /cv/personalize``.
    """
    candidate_summary = _build_candidate_summary(candidate)
    is_spanish = language.lower() in ("es", "es-es", "spanish", "español")
    lang_instruction = (
        "7. CRITICAL LANGUAGE REQUIREMENT: Write the entire CV content (profile statement, job titles, experience bullets, skill group labels, descriptions) in SPANISH. Set cv.language = 'es'."
        if is_spanish else
        "7. CRITICAL LANGUAGE REQUIREMENT: Write the entire CV content (profile statement, job titles, experience bullets, skill group labels, descriptions) in ENGLISH. Set cv.language = 'en'."
    )

    system = (
        "You are an expert CV writer. Generate a complete, polished CV document "
        "in JSON format according to the provided schema.\n\n"
        + APPLY_GUARDRAIL
        + "\n"
        + XYZ_GUIDANCE
        + "\n"
        + ATS_GUARDRAIL
        + "\n\n"
        + "Your output MUST have the following structure:\n"
        + json.dumps(GenerateCVOutput.model_json_schema(), indent=2)
    )

    user = f"""
Generate a generic base CV for the following candidate.

=== CANDIDATE PROFILE ===
{candidate_summary}

=== INSTRUCTIONS ===
1. Present experience bullets using the Action + Context + Result format. Strictly use the technologies and achievements stated for each job.
2. Group all declared skills into clean, standard categories.
3. Include ALL certifications from the profile in the cv.certifications array.
4. Include candidate languages in the skill groups or appropriate section.
5. Include personal website/portfolio, GitHub, and LinkedIn links in header fields.
6. Generate a compelling, truthful profile statement reflecting the candidate's actual background.
{lang_instruction}
8. Do NOT invent technologies, metrics, or additional bullets not supported by the candidate profile.
9. CRITICAL DENSITY & 1-PAGE TARGET: Keep bullets concise, punchy and high-impact (max 3-4 bullets per role). The CV must fit cleanly on ONE single page when rendered. Avoid wordy, verbose sentences.

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
        "job description. Your output must follow this schema:\n" + json.dumps(CVAnalysis.model_json_schema(), indent=2)
    )

    user = f"""
Analyze how well the candidate matches the job description below.

=== CANDIDATE PROFILE ===
{candidate_summary}

=== JOB DESCRIPTION ===
{job_description_text}

=== INSTRUCTIONS ===
1. match_score: estimate a 0–100 score for overall fit
2. missing_keywords: EXACTLY 5 — top keywords to emphasize, only genuinely supported ones
3. red_flags: EXACTLY 3 — top concerns a recruiter would raise
4. adapted_experience: 3–5 reframing ideas for EXISTING bullets (X-Y-Z, keyword placement)

Never suggest adding new achievements, metrics, or sections.

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
    language: str = "es",
) -> list[dict[str, str]]:
    """Build the personalize drafter prompt — tailors a CV to free-text job text."""
    candidate_summary = _build_candidate_summary(candidate)
    is_spanish = language.lower() in ("es", "es-es", "spanish", "español")
    lang_instruction = (
        "7. CRITICAL LANGUAGE REQUIREMENT: Write the entire CV content in SPANISH. Set cv.language = 'es'."
        if is_spanish else
        "7. CRITICAL LANGUAGE REQUIREMENT: Write the entire CV content in ENGLISH. Set cv.language = 'en'."
    )

    system = (
        "You are an expert CV writer. Generate a complete, tailored CV document "
        "in JSON format according to the provided schema.\n\n"
        + APPLY_GUARDRAIL
        + "\n"
        + XYZ_GUIDANCE
        + "\n\n"
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
1. PRESERVE the candidate's real experience — rewrite the EXISTING bullets with the X-Y-Z formula
   ("Accomplished X by doing Y, resulting in Z")
2. Weave the missing keywords into the EXISTING bullets ONLY where genuinely true
3. Address the red flags by honest reframing — never hide or invent
4. Apply the adapted_experience reframing suggestions where defensible
5. Keep the profile statement close to the original — adjust only to mention the target role
6. Do NOT add sections, roles, projects, skills, or metrics not in the candidate's profile
{lang_instruction}
8. Do NOT include a cover letter — output only the CV

CRITICAL: Do NOT expand the CV. Keep the CV concise, high-impact and fit on ONE page (max 3-4 bullets per role).

Output a valid GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def _drop_cover_letter(output_dict: dict[str, Any]) -> None:
    """Remove the cover letter from a generated CV output, if the LLM added one.

    The adapt/personalize flows produce a CV only — a cover letter is never
    rendered or persisted from these paths. The LLM occasionally includes one
    despite the prompt; this is the hard guarantee.
    """
    cv = output_dict.get("cv")
    if isinstance(cv, dict) and cv.get("cover_letter"):
        cv["cover_letter"] = None


# ── Directed retry (CAPA 3 — CVLinter) ────────────────────────────────


def build_lint_retry_prompt(
    old_cv: dict[str, Any],
    issues: list[str],
) -> list[dict[str, str]]:
    """Directed retry prompt — tells the model exactly what to fix (no blind retry).

    The prompt is deliberately short (old CV JSON + the issue list), so the
    correction call is cheaper and faster than the original drafter call.
    """
    cv_text = json.dumps(old_cv, indent=2, ensure_ascii=False)

    system = (
        "You are an expert CV writer. Your previous output had quality issues.\n\n"
        + APPLY_GUARDRAIL
        + "\n"
        + XYZ_GUIDANCE
    )

    user = f"""
The previous CV output had quality issues. Fix ONLY the following:
{"".join(f"- {issue}\n" for issue in issues)}
=== CURRENT CV JSON (keep its structure) ===
{cv_text}

Do not change anything else. Output the corrected GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


async def _lint_and_directed_retry(
    output_dict: dict[str, Any],
    candidate: CandidateProfile,
    provider_config: dict[str, Any],
    usage: dict | None = None,
) -> dict[str, Any]:
    """Run the deterministic CVLinter; on issues, ask the model to fix exactly those.

    One directed retry at most — if the corrected output fails to parse/validate
    or still carries issues, the original output is kept (the JSON is the source
    of truth; quality issues degrade gracefully instead of failing the request).
    """
    issues = lint_cv(output_dict, candidate)
    if not issues:
        return output_dict

    logger.warning("CVLinter flagged %d issue(s) — running directed retry", len(issues))
    try:
        corrected = await _llm_json(
            build_lint_retry_prompt(output_dict, issues),
            GenerateCVOutput,
            provider_config,
            temperature=0.3,
            max_tokens=8000,
            usage=usage,
        )
    except LLMError:
        logger.exception("Directed retry failed to produce valid output — keeping original CV")
        return output_dict
    return corrected


# ── LLM generation functions (with sanitizer) ────────────────────────


async def _llm_json(
    messages: list[dict[str, str]],
    schema_type: type,
    provider_config: dict[str, Any],
    temperature: float = 0.3,
    max_tokens: int = 4000,
    field_constraints: dict | None = None,
    web_search: bool = False,
    usage: dict | None = None,
) -> dict[str, Any]:
    """Call LLM, sanitize response, validate against Pydantic schema.

    Uses ``llm_completion()`` (no response_format) + ``sanitize_llm_response()``
    so the JSON repair pipeline handles malformed LLM output before Pydantic.
    When ``web_search=True`` the request goes through the Responses API with
    the OpenAI ``web_search`` tool, so a URL in the prompt is read by the
    provider's own infrastructure — never scraped from our servers.

    ``usage`` (optional) is a sink dict accumulating real token/cost usage.
    """
    provider = provider_config.get("provider", "anthropic")
    model = provider_config.get("model", "claude-sonnet-4-20250514")
    api_key = provider_config.get("api_key")

    if web_search:
        raw = await llm_completion_with_web_search(
            messages=messages,
            provider=provider,
            model=model,
            api_key=api_key,
            max_tokens=max_tokens,
            usage=usage,
        )
    else:
        raw = await llm_completion(
            messages=messages,
            provider=provider,
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            usage=usage,
        )

    constraints = field_constraints or default_field_constraints()
    try:
        cleaned = sanitize_llm_response(raw, schema_type.__name__, constraints)
    except ValueError as first_exc:
        # Retry once: large schemas often get truncated (max_tokens) or wrapped
        # in prose. Ask for the complete JSON only, with a larger token budget.
        logger.warning(
            "LLM response unparseable for %s — retrying once. %s",
            schema_type.__name__,
            str(first_exc)[:160],
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
            usage=usage,
        )
        try:
            cleaned = sanitize_llm_response(raw, schema_type.__name__, constraints)
        except ValueError as exc:
            raise LLMError(f"LLM response could not be parsed for {schema_type.__name__} (after retry): {exc}") from exc

    try:
        schema_type.model_validate(cleaned)
        return cleaned
    except ValidationError as exc:
        raise LLMError(f"LLM response failed {schema_type.__name__} validation after sanitization: {exc}") from exc


async def generate_cv(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None = None,
    provider_config: dict[str, Any] | None = None,
    temperature: float = 0.3,
    usage: dict | None = None,
) -> GenerateCVOutput:
    """Generate a full CV (without cover letter) as ``GenerateCVOutput``."""
    provider_config = provider_config or {}
    messages = build_json_drafter_prompt(candidate, job, evaluation)
    raw_dict = await _llm_json(
        messages,
        GenerateCVOutput,
        provider_config,
        temperature=temperature,
        max_tokens=8000,
        usage=usage,
    )
    return GenerateCVOutput(**raw_dict)


async def generate_cover_letter(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None = None,
    provider_config: dict[str, Any] | None = None,
    usage: dict | None = None,
) -> CoverLetter | None:
    """Generate a cover letter as a ``CoverLetter`` object."""
    provider_config = provider_config or {}
    messages = build_json_cover_letter_prompt(candidate, job, evaluation)
    try:
        raw_dict = await _llm_json(
            messages,
            CoverLetter,
            provider_config,
            temperature=0.4,
            max_tokens=2000,
            usage=usage,
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
    usage: dict | None = None,
) -> ReviewFeedback:
    """Review the generated CV JSON (fresh context, no drafter reasoning)."""
    provider_config = provider_config or {}
    messages = build_json_review_prompt(cv_json, candidate, job, evaluation)
    raw_dict = await _llm_json(
        messages,
        ReviewFeedback,
        provider_config,
        temperature=0.0,
        max_tokens=4000,
        usage=usage,
    )
    return ReviewFeedback(**raw_dict)


async def generate_revision(
    old_cv: dict[str, Any],
    review_feedback: ReviewFeedback,
    candidate: CandidateProfile,
    job: JobPosting,
    provider_config: dict[str, Any] | None = None,
    temperature: float = 0.2,
    usage: dict | None = None,
) -> GenerateCVOutput:
    """Revise the CV JSON based on reviewer feedback."""
    provider_config = provider_config or {}
    messages = build_json_revise_prompt(
        old_cv,
        review_feedback,
        candidate,
        job,
    )
    raw_dict = await _llm_json(
        messages,
        GenerateCVOutput,
        provider_config,
        temperature=temperature,
        max_tokens=8000,
        usage=usage,
    )
    return GenerateCVOutput(**raw_dict)


# ── CV generator LLM functions (FASE 1) ──────────────────────────────


async def generate_base_cv_llm(
    candidate: CandidateProfile,
    provider_config: dict[str, Any] | None = None,
    usage: dict | None = None,
    language: str = "es",
) -> dict[str, Any]:
    """Generate a generic base CV (``GenerateCVOutput``) with no job context."""
    provider_config = provider_config or {}
    messages = build_base_cv_prompt(candidate, language=language)
    raw_dict = await _llm_json(
        messages,
        GenerateCVOutput,
        provider_config,
        temperature=0.3,
        max_tokens=8000,
        usage=usage,
    )
    return await _lint_and_directed_retry(raw_dict, candidate, provider_config, usage=usage)


async def personalize_cv_llm(
    candidate: CandidateProfile,
    job_description_text: str,
    provider_config: dict[str, Any] | None = None,
    usage: dict | None = None,
    language: str = "es",
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
        usage=usage,
    )
    analysis = CVAnalysis(**analysis_dict)

    output_dict = await _llm_json(
        build_personalize_drafter_prompt(candidate, job_description_text, analysis, language=language),
        GenerateCVOutput,
        provider_config,
        temperature=0.3,
        max_tokens=8000,
        usage=usage,
    )
    _drop_cover_letter(output_dict)
    output_dict = await _lint_and_directed_retry(output_dict, candidate, provider_config, usage=usage)
    return analysis_dict, output_dict


# ── CV adapter LLM functions (FASE — Perfil → CV base → CV adaptado) ────


def _build_adapt_job_summary(job: JobPosting) -> str:
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
        parts.append("\nRequirements:\n" + "\n".join(f"  • {r}" for r in job.requirements[:15]))
    return "\n".join(parts)


def build_adapt_analysis_prompt(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    job: JobPosting,
) -> list[dict[str, str]]:
    """Recruiter-lens analysis using the base CV as the candidate representation."""
    candidate_summary = _build_candidate_summary(candidate)
    base_cv_text = json.dumps(base_cv_json, indent=2, ensure_ascii=False)
    job_summary = _build_adapt_job_summary(job)

    system = (
        "You are a technical recruiter analyzing a candidate's base CV against a "
        "job posting. Your output must follow this schema:\n" + json.dumps(CVAnalysis.model_json_schema(), indent=2)
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
2. missing_keywords: EXACTLY 5 — top keywords to emphasize, only genuinely supported ones
3. red_flags: EXACTLY 3 — top concerns a recruiter would raise
4. adapted_experience: 3–5 reframing ideas for EXISTING bullets (X-Y-Z, keyword placement)

Never suggest adding new achievements, metrics, or sections.

Output a valid CVAnalysis JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_adapt_drafter_prompt(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    job: JobPosting,
    analysis: CVAnalysis,
    language: str = "es",
) -> list[dict[str, str]]:
    """Drafter prompt — adapts the base CV to the job posting (never invents)."""
    base_cv_text = json.dumps(base_cv_json, indent=2, ensure_ascii=False)
    job_summary = _build_adapt_job_summary(job)
    candidate_summary = _build_candidate_summary(candidate)
    is_spanish = language.lower() in ("es", "es-es", "spanish", "español")
    lang_instruction = (
        "7. CRITICAL LANGUAGE REQUIREMENT: Write the entire CV content in SPANISH. Set cv.language = 'es'."
        if is_spanish else
        "7. CRITICAL LANGUAGE REQUIREMENT: Write the entire CV content in ENGLISH. Set cv.language = 'en'."
    )

    system = (
        "You are an expert CV writer. Adapt a candidate's BASE CV to a specific "
        "job posting, outputting a new tailored CV document in JSON format.\n\n"
        + APPLY_GUARDRAIL
        + "\n"
        + XYZ_GUIDANCE
        + "\n\n"
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
1. PRESERVE the base CV's structure and length — same sections, same entries, same bullet count
2. Rewrite the EXISTING experience bullets with the X-Y-Z formula
   ("Accomplished X by doing Y, resulting in Z")
3. Weave the missing keywords into the EXISTING bullets ONLY where genuinely true
4. Address the red flags by honest reframing — never hide or invent
5. Apply the adapted_experience reframing suggestions where defensible
6. Keep the profile statement close to the base CV — adjust only to mention the target role
{lang_instruction}
8. Do NOT include a cover letter — output only the CV

CRITICAL DENSITY & 1-PAGE TARGET: Keep the CV concise, high-impact and fit on ONE page (max 3-4 bullets per role).

Output a valid GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


async def adapt_cv_llm(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    job: JobPosting,
    provider_config: dict[str, Any] | None = None,
    usage: dict | None = None,
    language: str = "es",
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
        usage=usage,
    )
    analysis = CVAnalysis(**analysis_dict)

    output_dict = await _llm_json(
        build_adapt_drafter_prompt(candidate, base_cv_json, job, analysis, language=language),
        GenerateCVOutput,
        provider_config,
        temperature=0.3,
        max_tokens=8000,
        usage=usage,
    )
    _drop_cover_letter(output_dict)
    output_dict = await _lint_and_directed_retry(output_dict, candidate, provider_config, usage=usage)
    return analysis_dict, output_dict


# ── Adapt by URL (all plans — the model reads the link, we never scrape) ────


def build_adapt_url_analysis_prompt(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    url: str,
) -> list[dict[str, str]]:
    """Recruiter-lens analysis against a job posting referenced by URL.

    The job content is NOT fetched by us: the prompt points the model at the
    URL and its ``web_search`` tool reads it (the provider's infrastructure,
    under its own agreements).
    """
    candidate_summary = _build_candidate_summary(candidate)
    base_cv_text = json.dumps(base_cv_json, indent=2, ensure_ascii=False)

    system = (
        "You are a technical recruiter analyzing a candidate's base CV against a "
        "job posting. Your output must follow this schema:\n" + json.dumps(CVAnalysis.model_json_schema(), indent=2)
    )

    user = f"""
Analyze how well the candidate matches the job posting below.

=== CANDIDATE BASE CV ===
{base_cv_text[:6000]}

=== CANDIDATE PROFILE ===
{candidate_summary}

=== JOB POSTING ===
The job posting is published at the following URL:
{url}

Use your web search capability to open that URL and read the full job posting
(title, company, description, requirements). Analyze the candidate's fit
against the real content you find there. If the page is unreachable, analyze
based on whatever you can learn about the role from the URL.

=== INSTRUCTIONS ===
1. match_score: estimate a 0–100 score for overall fit
2. missing_keywords: EXACTLY 5 — top keywords to emphasize, only genuinely supported ones
3. red_flags: EXACTLY 3 — top concerns a recruiter would raise
4. adapted_experience: 3–5 reframing ideas for EXISTING bullets (X-Y-Z, keyword placement)

Never suggest adding new achievements, metrics, or sections.

Output a valid CVAnalysis JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_adapt_url_drafter_prompt(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    url: str,
    analysis: CVAnalysis,
    language: str = "es",
) -> list[dict[str, str]]:
    """Drafter prompt — adapts the base CV to the job at the given URL."""
    base_cv_text = json.dumps(base_cv_json, indent=2, ensure_ascii=False)
    candidate_summary = _build_candidate_summary(candidate)
    is_spanish = language.lower() in ("es", "es-es", "spanish", "español")
    lang_instruction = (
        "7. CRITICAL LANGUAGE REQUIREMENT: Write the entire CV content in SPANISH. Set cv.language = 'es'."
        if is_spanish else
        "7. CRITICAL LANGUAGE REQUIREMENT: Write the entire CV content in ENGLISH. Set cv.language = 'en'."
    )

    system = (
        "You are an expert CV writer. Adapt a candidate's BASE CV to a specific "
        "job posting, outputting a new tailored CV document in JSON format.\n\n"
        + APPLY_GUARDRAIL
        + "\n"
        + XYZ_GUIDANCE
        + "\n\n"
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
The job posting is published at the following URL:
{url}

Use your web search capability to open that URL and read the full job posting
(title, company, description, requirements) before adapting.

=== RECRUITER ANALYSIS ===
{analysis.model_dump_json(indent=2)}

=== INSTRUCTIONS ===
1. PRESERVE the base CV's structure and length — same sections, same entries, same bullet count
2. Rewrite the EXISTING experience bullets with the X-Y-Z formula
   ("Accomplished X by doing Y, resulting in Z")
3. Weave the missing keywords into the EXISTING bullets ONLY where genuinely true
4. Address the red flags by honest reframing — never hide or invent
5. Apply the adapted_experience reframing suggestions where defensible
6. Keep the profile statement close to the base CV — adjust only to mention the target role
{lang_instruction}
8. Do NOT include a cover letter — output only the CV

CRITICAL DENSITY & 1-PAGE TARGET: Keep the CV concise, high-impact and fit on ONE page (max 3-4 bullets per role).

Output a valid GenerateCVOutput JSON object.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


async def adapt_cv_llm_with_url(
    candidate: CandidateProfile,
    base_cv_json: dict[str, Any],
    url: str,
    provider_config: dict[str, Any] | None = None,
    usage: dict | None = None,
    language: str = "es",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Two-step adapt pipeline where the job text is read from a URL by the model.

    The URL goes into the prompt and the provider's ``web_search`` tool fetches
    the page under its own agreements — our backend never scrapes. Requires a
    web-search capable model (e.g. ``gpt-5``); otherwise raises
    ``PreconditionError`` before any LLM call.

    Returns ``(analysis_dict, output_dict)``.
    """
    provider_config = provider_config or {}
    model_ref = f"{provider_config.get('provider', '')}/{provider_config.get('model', '')}".strip("/")
    if not has_web_search_support(model_ref):
        raise WebSearchUnavailableError(
            "The configured AI model can't open links. Use a model with web "
            "search (e.g. gpt-5) or paste the job description instead."
        )

    analysis_dict = await _llm_json(
        build_adapt_url_analysis_prompt(candidate, base_cv_json, url),
        CVAnalysis,
        provider_config,
        temperature=0.2,
        max_tokens=2000,
        web_search=True,
        usage=usage,
    )
    analysis = CVAnalysis(**analysis_dict)

    output_dict = await _llm_json(
        build_adapt_url_drafter_prompt(candidate, base_cv_json, url, analysis, language=language),
        GenerateCVOutput,
        provider_config,
        temperature=0.3,
        max_tokens=8000,
        web_search=True,
        usage=usage,
    )
    _drop_cover_letter(output_dict)
    output_dict = await _lint_and_directed_retry(output_dict, candidate, provider_config, usage=usage)
    return analysis_dict, output_dict
