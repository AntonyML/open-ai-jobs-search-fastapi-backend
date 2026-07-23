"""Apply service — generates tailored CV and cover letter.

Implements the /apply workflow with a 3-stage Drafter-Reviewer pipeline:
1. DRAFT: Generate tailored experience + cover letter (existing)
2. REVIEW: Second LLM call critiques the rendered drafts (NEW)
3. REVISE: Apply review feedback and regenerate (NEW)
4. COMPILE: LaTeX compilation and verification (existing)

Architecture decision:
We use SEPARATE LLM calls for draft, review, and revise so that:
- The reviewer has a fresh context window (no bias from the draft prompt)
- Each stage can use different temperature (draft=0.3, review=0.0 for reproducibility, revise=0.2)
- The review stage explicitly checks for fabricated content, missing keywords, and weak framing
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import Application, CandidateProfile, JobPosting, RankEvaluation, User
from app.exceptions import LLMError, LatexCompileError, NotFoundError, ProfileIncompleteError
from app.llm.adapter import llm_completion, llm_completion_structured, get_provider_kwargs
from app.services import ats_check, cv_cutter, pdf_compiler
from app.schemas.apply import (
    AddressedRedFlag,
    ApplyResult,
    CoverLetterLLMOutput,
    IncorporatedKeyword,
    ReviewFeedback,
    ReviewIssue,
    ReviseAction,
    ReviseResult,
    TailoredExperienceEntry,
    TailoredExperienceLLMOutput,
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


# ── LaTeX template paths ────────────────────────────────────────────

CV_TEMPLATE_DIR = settings.latex_cv_dir
COVER_TEMPLATE_DIR = settings.latex_cover_dir

CV_MASTER_TEMPLATE = CV_TEMPLATE_DIR / "main_example.tex"
COVER_MASTER_TEMPLATE = COVER_TEMPLATE_DIR / "cover_example.tex"
COVER_CLS = COVER_TEMPLATE_DIR / "cover.cls"
OPENFONTS_DIR = COVER_TEMPLATE_DIR / "OpenFonts"


# ── LaTeX binary resolution ─────────────────────────────────────────


def _resolve_latex_binary(name: str) -> str | Path:
    """Resolve the full path to a LaTeX-related binary.

    If ``settings.latex_bin_dir`` is set (e.g. MiKTeX Portable), return the
    full path to the binary inside that directory. Otherwise return the
    bare name so the system PATH is used.

    Args:
        name: Binary name without extension (e.g. ``"lualatex"``).

    Returns:
        ``Path`` to the binary if ``latex_bin_dir`` is configured, otherwise
        the bare ``name`` string.
    """
    if settings.latex_bin_dir:
        bin_dir = Path(settings.latex_bin_dir)
        windows_path = bin_dir / f"{name}.exe"
        if windows_path.exists():
            return windows_path
        linux_path = bin_dir / name
        if linux_path.exists():
            return linux_path
        import sys
        return windows_path if sys.platform == "win32" else linux_path
    return name


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

Return JSON with TailoredExperienceLLMOutput schema.
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

Return JSON with CoverLetterLLMOutput schema.
"""

    user_prompt = "Generate the cover letter content for this job application."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_review_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
    cv_latex: str,
    cover_letter_latex: str,
    tailored_experience: list[TailoredExperienceEntry],
    cover_letter_content: CoverLetterLLMOutput,
    company_research: str | None = None,
) -> list[dict[str, str]]:
    """Build prompt for the reviewer agent.

    The reviewer receives the FULL rendered LaTeX of both documents,
    the job posting, candidate profile, and optional company research
    context. It must return structured feedback including any issues found.

    Company research is included so the reviewer can verify cover letter
    claims about the target company (products, mission, recent news).

    Temperature 0 is used for reproducibility.
    """
    candidate_summary = _build_candidate_summary_for_apply(candidate)
    job_summary = _build_job_summary_for_apply(job)
    missing_keywords = evaluation.missing_keywords or []

    company_context = ""
    if company_research:
        company_context = f"\nCOMPANY RESEARCH (verified facts about {job.company}):\n{company_research}\n"
        company_context += "Use this context to verify claims in the cover letter. \n"
        company_context += "Flag any cover letter claims about the company that are NOT supported by this research.\n"

    system_prompt = f"""{REVIEWER_GUARDRAIL}

CANDIDATE PROFILE (ground truth — compare everything against this):
{candidate_summary}

JOB POSTING:
{job_summary}

RANK EVALUATION:
- Missing keywords that should have been incorporated: {', '.join(missing_keywords) if missing_keywords else 'None'}
- Verdict: {evaluation.verdict} ({evaluation.overall_score}/100)
{company_context}
DRAFT CV (full rendered LaTeX):
```latex
{cv_latex[:4000]}
```

DRAFT COVER LETTER (full rendered LaTeX):
```latex
{cover_letter_latex[:3000]}
```

TASK:
Review these draft documents critically. Return structured JSON with ReviewFeedback schema.
Be specific — reference exact bullet points, paragraph sections, and keywords.
"""

    user_prompt = f"Review the draft CV and cover letter for the {job.title} role at {job.company}."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_revise_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
    tailored_experience: list[TailoredExperienceEntry],
    cover_letter_content: CoverLetterLLMOutput,
    review_feedback: ReviewFeedback,
) -> list[dict[str, str]]:
    """Build prompt for the revise step.

    Takes the original draft content PLUS the reviewer's feedback, and
    produces improved versions addressing every issue.

    The LLM returns the revised TailoredExperienceLLMOutput (same schema
    as the draft step) so the render functions can use it directly.
    A separate ReviseResult describes what changed.
    """
    candidate_summary = _build_candidate_summary_for_apply(candidate)
    job_summary = _build_job_summary_for_apply(job)
    missing_keywords = evaluation.missing_keywords or []

    # Format current draft for reference
    exp_lines = []
    for exp in tailored_experience:
        exp_lines.append(f"\n{exp.title} at {exp.company}")
        for bullet in exp.bullets:
            exp_lines.append(f"  • {bullet}")
    draft_exp_text = "\n".join(exp_lines)

    # Format review issues
    issues_text = "\n".join(
        f"- [{i.severity.upper()}] [{i.location}] {i.description}"
        + (f" — {i.suggestion}" if i.suggestion else "")
        for i in review_feedback.issues
    )

    system_prompt = f"""{REVISE_GUARDRAIL}

CANDIDATE PROFILE (ground truth):
{candidate_summary}

JOB POSTING:
{job_summary}

REQUIRED KEYWORDS (must incorporate where genuinely true):
{', '.join(missing_keywords) if missing_keywords else 'None'}

CURRENT DRAFT EXPERIENCE:
{draft_exp_text}

REVIEWER FEEDBACK:
Overall: {review_feedback.overall_assessment}

Passes:
{chr(10).join('- ' + p for p in review_feedback.passes) if review_feedback.passes else '(none listed)'}

Issues to fix:
{issues_text if issues_text else '(none — documents look good)'}

Missed keywords: {', '.join(review_feedback.missed_keywords) if review_feedback.missed_keywords else 'None'}

Strong recommendations (priority order):
{chr(10).join('{i+1}. ' + r for i, r in enumerate(review_feedback.strong_recommendations)) if review_feedback.strong_recommendations else '(none specific)'}

TASK:
1. Fix every issue the reviewer identified, prioritizing high-severity issues
2. Incorporate missed keywords where genuinely true for the candidate
3. Preserve X-Y-Z formula in all bullets
4. NEVER fabricate experience
5. If an issue cannot be fixed honestly, note it as a remaining concern

Return BOTH the revised TailoredExperienceLLMOutput AND the ReviseResult.
The changed entries will be used to re-render the CV and cover letter.
The cover letter should also be updated to match the revised experience.
"""

    user_prompt = "Revise the draft experience section based on reviewer feedback."

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


# ── LaTeX rendering ─────────────────────────────────────────────────


def render_cv_latex(
    candidate: CandidateProfile,
    tailored_experience: list[TailoredExperienceEntry],
    job: JobPosting,
) -> str:
    """Render the CV LaTeX from the master template."""
    template = CV_MASTER_TEMPLATE.read_text(encoding="utf-8")

    first_name = candidate.full_name.split()[0] if candidate.full_name else "[First]"
    last_name = " ".join(candidate.full_name.split()[1:]) if candidate.full_name and len(candidate.full_name.split()) > 1 else "[Last]"
    full_name = candidate.full_name or "[YOUR_NAME]"
    replacements = {
        "[First]": first_name,
        "[Last]": last_name,
        "[YOUR_NAME]": full_name,
        "[Your Address, City, Country]": candidate.location or "[Your Address, City, Country]",
        "[+XX XXXXXXXXXX]": candidate.phone or "[+XX XXXXXXXXXX]",
        "[your.email@example.com]": candidate.email or "[your.email@example.com]",
        "[https://linkedin.com/in/your-profile]": candidate.linkedin_url or "[https://linkedin.com/in/your-profile]",
        "[https://github.com/your-username]": candidate.github_url or "[https://github.com/your-username]",
    }

    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)

    profile_stmt = candidate.profile_statement or "Experienced professional seeking new opportunities."
    template = re.sub(
        r"\\small\{.*?Profile statement.*?\}",
        lambda m: f"\\small{{{profile_stmt}}}",
        template,
        flags=re.DOTALL,
    )

    skills_section = _build_skills_section(candidate)
    template = re.sub(
        r"\\section{Core Competencies}.*?\\section{Professional Experience}",
        lambda m: f"\\section{{Core Competencies}}\n\\vspace{{1pt}}\n{skills_section}\n\n\\section{{Professional Experience}}",
        template,
        flags=re.DOTALL,
    )

    exp_section = _build_experience_section(tailored_experience)
    template = re.sub(
        r"\\section{Professional Experience}.*?\\section{Education}",
        lambda m: f"\\section{{Professional Experience}}\n\\vspace{{3pt}}\n{exp_section}\n\n\\section{{Education}}",
        template,
        flags=re.DOTALL,
    )

    edu_section = _build_education_section(candidate)
    template = re.sub(
        r"\\section{Education}.*?\\section{Selected Publications}",
        lambda m: f"\\section{{Education}}\n\\vspace{{3pt}}\n{edu_section}\n\n\\section{{Selected Publications}}",
        template,
        flags=re.DOTALL,
    )

    pub_section = _build_publications_section(candidate)
    template = re.sub(
        r"\\section{Selected Publications}.*?\\section{Honors and Awards}",
        lambda m: f"\\section{{Selected Publications}}\n\\vspace{{3pt}}\n{pub_section}\n\n\\section{{Honors and Awards}}",
        template,
        flags=re.DOTALL,
    )

    awards_section = _build_awards_section(candidate)
    template = re.sub(
        r"\\section{Honors and Awards}.*?\\section{References}",
        lambda m: f"\\section{{Honors and Awards}}\n\\vspace{{3pt}}\n{awards_section}\n\n\\section{{References}}",
        template,
        flags=re.DOTALL,
    )

    ref_section = _build_references_section(candidate)
    template = re.sub(
        r"\\section{References}.*?\\end{document}",
        lambda m: f"\\section{{References}}\n\\vspace{{3pt}}\n{ref_section}\n\n\\end{{document}}",
        template,
        flags=re.DOTALL,
    )

    return template


def _build_skills_section(candidate: CandidateProfile) -> str:
    if not candidate.skills:
        return "\\item \\textbf{Skills}: Not specified"

    items = []
    if candidate.skills.get("programming_ml"):
        langs = [f"{s.get('language', '')} ({s.get('proficiency', '')})" for s in candidate.skills["programming_ml"]]
        items.append(f"\\item \\textbf{{Programming & ML}}: {', '.join(langs)}")
    if candidate.skills.get("domain_expertise"):
        items.append(f"\\item \\textbf{{Domain Expertise}}: {', '.join(candidate.skills['domain_expertise'])}")
    if candidate.skills.get("software_tools"):
        items.append(f"\\item \\textbf{{Software & Tools}}: {', '.join(candidate.skills['software_tools'])}")

    if not items:
        return "\\item \\textbf{Skills}: Not specified"

    return "\\begin{itemize}\n" + "\n\n".join(items) + "\n\\end{itemize}"


def _build_experience_section(experience: list[TailoredExperienceEntry]) -> str:
    if not experience:
        return "\\item{\\cventry{}{}{}{}{}{\\vspace{1pt}\\begin{itemize}\\item No experience specified\\end{itemize}}}"

    entries = []
    for exp in experience:
        date_range = f"{exp.start_date or ''}--{exp.end_date or 'Present'}"
        location = exp.location or ""
        bullets = "\n".join(f"    \\item {bullet}" for bullet in exp.bullets)

        entry = f"""\\item{{\\cventry{{{date_range}}}{{{exp.title}}}{{{exp.company}}}{{{location}}}{{}}{{\\vspace{{1pt}}
\\begin{{itemize}}
{bullets}
\\end{{itemize}}}}}}"""
        entries.append(entry)

    return "\n\\vspace{3pt}\n".join(entries)


def _build_education_section(candidate: CandidateProfile) -> str:
    if not candidate.education:
        return "\\item{\\cventry{}{}{}{}{}{}}"

    entries = []
    for edu in candidate.education:
        period = edu.get("period", "")
        institution = edu.get("institution", "")
        degree = edu.get("degree", "")
        topics = edu.get("key_topics", "")

        entry = f"\\item{{\\cventry{{{period}}}{{{degree}}}{{{institution}}}{{}}{{}}{{\\vspace{{1pt}}\\begin{{itemize}}\\item {topics}\\end{{itemize}}}}}}"
        entries.append(entry)

    return "\n\\vspace{3pt}\n".join(entries)


def _build_publications_section(candidate: CandidateProfile) -> str:
    if not candidate.publications:
        return "\\item No publications listed."

    items = []
    for pub in candidate.publications:
        authors = pub.get("authors", "")
        year = pub.get("year", "")
        title = pub.get("title", "")
        journal = pub.get("journal", "")
        doi = pub.get("doi", "")

        line = f"\\item {authors} ({year}). {title}. {journal}."
        if doi:
            line += f" \\href{{{doi}}}{{DOI}}"
        items.append(line)

    return "\\begin{itemize}\n" + "\n".join(items) + "\n\\end{itemize}"


def _build_awards_section(candidate: CandidateProfile) -> str:
    if not candidate.awards:
        return "\\item No awards listed."

    items = []
    for award in candidate.awards:
        name = award.get("award", "")
        event = award.get("event", "")
        year = award.get("year", "")
        items.append(f"\\item {name} - {event} ({year})")

    return "\\begin{itemize}\n" + "\n".join(items) + "\n\\end{itemize}"


def _build_references_section(candidate: CandidateProfile) -> str:
    if not candidate.references:
        return "\\item References available upon request."

    items = []
    for ref in candidate.references:
        name = ref.get("name", "")
        title = ref.get("title", "")
        company = ref.get("company", "")
        email = ref.get("email", "")
        phone = ref.get("phone", "")

        line = f"\\item {name}, {title}, {company}"
        if email:
            line += f" ({email}"
            if phone:
                line += f", {phone}"
            line += ")"
        elif phone:
            line += f" ({phone})"
        items.append(line)

    return "\\begin{itemize}\n" + "\n".join(items) + "\n\\end{itemize}"


def render_cover_letter_latex(
    candidate: CandidateProfile,
    job: JobPosting,
    cover_letter_content: CoverLetterLLMOutput,
) -> str:
    """Render the cover letter LaTeX from the master template."""
    template = COVER_MASTER_TEMPLATE.read_text(encoding="utf-8")

    name_parts = candidate.full_name.split() if candidate.full_name else ["[First]", "[Last]"]
    first_name = name_parts[0]
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    replacements = {
        "[YOUR NAME]": candidate.full_name or "[YOUR NAME]",
        "[YOUR_EMAIL]": candidate.email or "[YOUR_EMAIL]",
        "your.email@example.com": candidate.email or "your.email@example.com",
        "[+XX XXXXXXXXXX]": candidate.phone or "[+XX XXXXXXXXXX]",
        "[https://www.linkedin.com/in/yourprofile]": candidate.linkedin_url or "[https://www.linkedin.com/in/yourprofile]",
        "https://www.linkedin.com/in/yourprofile": candidate.linkedin_url or "https://www.linkedin.com/in/yourprofile",
        "Dear [Hiring Manager / Team],": f"Dear {job.company or 'Hiring Team'},",
        "[Opening paragraph: name the role and where you found it, state your strongest connection to it in one sentence, and preview why you are a fit. Keep it to 2--3 sentences.]": cover_letter_content.opening_paragraph,
        "[Body paragraph: your most relevant experience, framed toward the tasks in the posting. Follow with 3--5 concrete bullets:]": cover_letter_content.body_paragraphs[0] if cover_letter_content.body_paragraphs else "",
        "[Connection paragraph: why this company specifically. Reference a verified specific: a product, a stated priority, a team. Never generic.]": cover_letter_content.company_connection_paragraph,
        "[Personal fit paragraph: behavioral strengths and what you bring to the team, 2--3 sentences.]": cover_letter_content.personal_fit_paragraph,
        "I look forward to hearing from you.": cover_letter_content.closing_paragraph,
        "[YOUR NAME]": candidate.full_name or "[YOUR NAME]",
    }

    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)

    bullets = cover_letter_content.body_paragraphs[1:] if len(cover_letter_content.body_paragraphs) > 1 else []
    if bullets:
        bullet_tex = _build_cover_letter_bullets(bullets)
        template = _insert_cover_letter_bullets(template, bullet_tex)

    return template


def _build_cover_letter_bullets(bullets: list[str]) -> str:
    if not bullets:
        return ""

    bullet_items = "\n".join(f"    \\item {bullet}" for bullet in bullets)
    return f"""\\vspace{{6pt}}

{{\\raggedright\\fontspec[Path = OpenFonts/fonts/raleway/]{{Raleway-Medium}}\\fontsize{{11pt}}{{13pt}}\\selectfont
\\begin{{itemize}}
{bullet_items}
\\end{{itemize}}\\par}}
\\vspace{{6pt}}"""


def _insert_cover_letter_bullets(template: str, bullet_tex: str) -> str:
    pattern = r"(\\lettercontent\{[^}]*\})(\s*)(\\lettercontent\{)"
    return re.sub(
        pattern,
        lambda m: m.group(1) + m.group(2) + bullet_tex + m.group(2) + m.group(3),
        template,
        count=1,
    )


# ── LaTeX compilation ───────────────────────────────────────────────


async def _compile_latex_raw(
    tex_content: str,
    output_dir: Path,
    job_name: str,
    engine: str = "lualatex",
) -> tuple[Path, int]:
    """Low-level LaTeX compilation — shared by ``compile_latex`` and
    ``compile_latex_get_pages``.

    Writes the .tex file, runs the compiler twice, and returns the
    PDF path plus actual page count. Does NOT check expected page
    count — the caller decides whether to enforce a limit.

    Raises:
        LatexCompileError: If compilation itself fails.
    """
    tex_file = output_dir / f"{job_name}.tex"
    pdf_file = output_dir / f"{job_name}.pdf"

    tex_file.write_text(tex_content, encoding="utf-8")

    engine_bin = _resolve_latex_binary(engine)
    for _ in range(2):
        proc = await asyncio.create_subprocess_exec(
            str(engine_bin),
            "-interaction=nonstopmode",
            "-output-directory",
            str(output_dir),
            str(tex_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_output = stderr.decode("utf-8", errors="replace")
            raise LatexCompileError(
                f"{engine} compilation failed for {job_name}: {error_output}"
            )

    if not pdf_file.exists():
        raise LatexCompileError(f"PDF not generated: {pdf_file}")

    actual_pages = await _get_pdf_page_count(pdf_file)
    return pdf_file, actual_pages


async def compile_latex_get_pages(
    tex_content: str,
    output_dir: Path,
    job_name: str,
    engine: str = "lualatex",
) -> tuple[Path, int]:
    """Compile LaTeX to PDF and return page count WITHOUT checking it.

    Unlike ``compile_latex()``, this function does NOT raise on wrong
    page count. It always returns the actual page count, which is
    useful for the CV cutter's iterative trim loop where we need to
    check how many pages the current version produces.

    Args:
        tex_content: The .tex file content
        output_dir: Directory to write output
        job_name: Base name for output files (without extension)
        engine: 'lualatex' or 'xelatex'

    Returns:
        Tuple of (pdf_path, actual_page_count)

    Raises:
        LatexCompileError: If compilation itself fails (not page count)
    """
    return await _compile_latex_raw(tex_content, output_dir, job_name, engine)


async def compile_latex(
    tex_content: str,
    output_dir: Path,
    job_name: str,
    engine: str = "lualatex",
    expected_pages: int = 2,
) -> tuple[Path, int]:
    """Compile LaTeX to PDF and verify page count.

    Delegates compilation to ``_compile_latex_raw``, then checks the
    returned page count against ``expected_pages``. Raises on mismatch.

    Args:
        tex_content: The .tex file content
        output_dir: Directory to write output
        job_name: Base name for output files (without extension)
        engine: 'lualatex' or 'xelatex'
        expected_pages: Expected page count (2 for CV, 1 for cover letter).
            Pass a very high number to skip the check (discouraged — use
            ``compile_latex_get_pages`` instead).

    Returns:
        Tuple of (pdf_path, actual_page_count)

    Raises:
        LatexCompileError: If compilation fails or page count is wrong.
    """
    pdf_file, actual_pages = await _compile_latex_raw(tex_content, output_dir, job_name, engine)

    if actual_pages != expected_pages:
        raise LatexCompileError(
            f"Wrong page count for {job_name}: expected {expected_pages}, got {actual_pages}"
        )

    return pdf_file, actual_pages


async def _get_pdf_page_count(pdf_path: Path) -> int:
    """Get page count of a PDF using pdftotext or pdfinfo."""
    pdfinfo_bin = _resolve_latex_binary("pdfinfo")
    pdftotext_bin = _resolve_latex_binary("pdftotext")
    for cmd in [[str(pdfinfo_bin), str(pdf_path)], [str(pdftotext_bin), str(pdf_path), "-"]]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                output = stdout.decode("utf-8", errors="replace")
                match = re.search(r"Pages:\s+(\d+)", output)
                if match:
                    return int(match.group(1))
                return output.count("\f") + 1
        except FileNotFoundError:
            continue
    return 1


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


# ── Drafter-Reviewer LLM functions ─────────────────────────────────


async def _generate_review(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
    cv_latex: str,
    cover_letter_latex: str,
    tailored_experience: list[TailoredExperienceEntry],
    cover_letter_content: CoverLetterLLMOutput,
    provider_config: dict | None = None,
    company_research: str | None = None,
) -> ReviewFeedback:
    """Call LLM REVIEWER to critique the draft documents.

    The reviewer runs with temperature 0 for reproducibility.
    It receives the FULL rendered LaTeX, job posting, candidate profile,
    and optional company research context. It returns structured feedback
    identifying issues.
    """
    messages = build_review_prompt(
        candidate, job, evaluation,
        cv_latex, cover_letter_latex,
        tailored_experience, cover_letter_content,
        company_research=company_research,
    )

    try:
        provider_kwargs = _get_provider_kwargs(provider_config)
        result: ReviewFeedback = await llm_completion_structured(
            messages=messages,
            output_schema=ReviewFeedback,
            **provider_kwargs,
            temperature=0.0,  # Deterministic for reproducibility
            max_tokens=2000,
        )
        return result
    except Exception as e:
        # If the reviewer fails, log but don't block the pipeline.
        # Return a fallback ReviewFeedback that honestl signals "review skipped".
        logger.warning(f"Reviewer LLM call failed — skipping review: {e}")
        return ReviewFeedback(
            overall_assessment="Review skipped due to LLM error — documents used as-is.",
            passes=[],
            issues=[],
            missed_keywords=[],
            strong_recommendations=[],
        )


async def _generate_revision(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
    tailored_experience: list[TailoredExperienceEntry],
    cover_letter_content: CoverLetterLLMOutput,
    review_feedback: ReviewFeedback,
    provider_config: dict | None = None,
) -> tuple[list[TailoredExperienceEntry], ReviseResult]:
    """Call LLM to apply reviewer feedback and produce revised experience.

    The revise step uses temperature 0.2 (slightly higher than review
    to allow creative rewrites, but lower than draft to stay close to the
    review guidance).

    Returns:
        Tuple of (revised_experience, revise_result with changes description).
    """
    messages = build_revise_prompt(
        candidate, job, evaluation,
        tailored_experience, cover_letter_content,
        review_feedback,
    )

    try:
        provider_kwargs = _get_provider_kwargs(provider_config)
        result: TailoredExperienceLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=TailoredExperienceLLMOutput,
            **provider_kwargs,
            temperature=0.2,
            max_tokens=3000,
        )

        # If the reviewer had no issues, just return the original
        if not review_feedback.issues:
            return tailored_experience, ReviseResult(
                changes_made=[ReviseAction(
                    issue_type="no_issues",
                    description="Reviewer found no issues — keeping original draft.",
                )],
                remaining_concerns=[],
                overall_quality_improvement="No changes needed.",
            )

        return result.tailored_experience, ReviseResult(
            changes_made=[
                ReviseAction(
                    issue_type=issue.type,
                    description=f"Addressed: {issue.description[:120]}",
                )
                for issue in review_feedback.issues[:10]
            ],
            remaining_concerns=review_feedback.missed_keywords,
            overall_quality_improvement=(
                f"Applied {min(len(review_feedback.issues), 10)} fix(es) "
                f"based on reviewer feedback."
            ),
        )
    except Exception as e:
        logger.warning(f"Revise LLM call failed — keeping original draft: {e}")
        return tailored_experience, ReviseResult(
            changes_made=[],
            remaining_concerns=["Revision skipped due to LLM error — using original draft."],
            overall_quality_improvement="Revision failed — original content preserved.",
        )


# ── Main orchestration ──────────────────────────────────────────────


async def execute_apply(
    db: AsyncSession,
    user_id: str,
    job_posting_id: str,
    rank_evaluation_id: str | None = None,
    cv_template: str = "moderncv-banking",
    cover_letter_template: str = "cover-cls",
    provider_config: dict | None = None,
    application: Application | None = None,
) -> ApplyResult:
    """Execute the full apply workflow with Drafter-Reviewer pipeline.

    Pipeline stages:
    1. DRAFT: Generate tailored experience + cover letter (2 LLM calls)
    2. RENDER DRAFT: Produce LaTeX from draft content (deterministic)
    3. REVIEW: Second agent critiques the draft (1 LLM call, temp=0)
    4. REVISE: Apply feedback and regenerate (1 LLM call, temp=0.2)
    5. RENDER FINAL: Produce final LaTeX (deterministic)
    6. COMPILE: LaTeX compilation and page count verification

    The pipeline_stage is persisted in the Application record so the
    frontend can show real-time progress.

    If ``application`` is provided, the pipeline updates it in-place
    (used by background task). Otherwise a new Application is created.
    """
    # 1. Load all dependencies in parallel
    job_fut = db.execute(
        select(JobPosting).where(
            JobPosting.id == job_posting_id,
            JobPosting.user_id == user_id,
        )
    )
    if rank_evaluation_id:
        eval_fut = db.execute(
            select(RankEvaluation).where(
                RankEvaluation.id == rank_evaluation_id,
                RankEvaluation.job_posting_id == job_posting_id,
            )
        )
    else:
        eval_fut = db.execute(
            select(RankEvaluation)
            .where(RankEvaluation.job_posting_id == job_posting_id)
            .order_by(RankEvaluation.created_at.desc())
        )
    cand_fut = db.execute(
        select(CandidateProfile)
        .options(selectinload(CandidateProfile.user))
        .where(CandidateProfile.user_id == user_id)
    )

    job_res, eval_res, cand_res = await asyncio.gather(job_fut, eval_fut, cand_fut)

    job = job_res.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job posting not found.")

    evaluation = eval_res.scalar_one_or_none()
    if evaluation is None:
        raise NotFoundError("Rank evaluation not found. Run /rank first.")

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
            cv_template=cv_template,
            cover_letter_template=cover_letter_template,
            language=job.language or "en",
            pipeline_stage="draft",
        )
        db.add(application)
        await db.commit()
        await db.refresh(application)
    else:
        application.rank_evaluation_id = evaluation.id
        application.cv_template = cv_template
        application.cover_letter_template = cover_letter_template
        application.language = job.language or "en"
        application.pipeline_stage = "draft"
        await db.commit()

    # ═══════════════════════════════════════════════════════════════
    # STAGE 1: DRAFT — Generate tailored experience + cover letter
    # ═══════════════════════════════════════════════════════════════

    tailored_experience = await _generate_tailored_experience(
        candidate, job, evaluation, provider_config
    )

    cover_letter_content = await _generate_cover_letter(
        candidate, job, evaluation, tailored_experience, provider_config
    )

    # ═══════════════════════════════════════════════════════════════
    # STAGE 2: RENDER DRAFT — Produce LaTeX from draft content
    # Save draft LaTeX for audit trail (pre-review content).
    # ═══════════════════════════════════════════════════════════════

    draft_cv_tex = render_cv_latex(candidate, tailored_experience, job)
    draft_cover_tex = render_cover_letter_latex(candidate, job, cover_letter_content)

    # Persist draft for audit trail and update stage
    application.pipeline_stage = "draft"
    application.draft_cv_tex = draft_cv_tex
    application.draft_cover_letter_tex = draft_cover_tex
    await db.commit()

    # ── Company research ──────────────────────────────────────────
    # Fetch basic company info so the reviewer can verify cover letter
    # claims about the target company.
    company_research = await _fetch_company_info(job, provider_config)

    # ═══════════════════════════════════════════════════════════════
    # STAGE 3: REVIEW — Second agent critiques the draft
    # ═══════════════════════════════════════════════════════════════

    review_feedback = await _generate_review(
        candidate, job, evaluation,
        draft_cv_tex, draft_cover_tex,
        tailored_experience, cover_letter_content,
        provider_config,
        company_research=company_research,
    )

    # Persist review feedback and update stage
    application.pipeline_stage = "reviewed"
    application.review_feedback = review_feedback.model_dump()
    application.review_issues = [i.model_dump() for i in review_feedback.issues]
    await db.commit()

    # ═══════════════════════════════════════════════════════════════
    # STAGE 4: REVISE — Apply feedback and regenerate
    # ═══════════════════════════════════════════════════════════════

    revised_experience, revise_result = await _generate_revision(
        candidate, job, evaluation,
        tailored_experience, cover_letter_content,
        review_feedback, provider_config,
    )

    # ═══════════════════════════════════════════════════════════════
    # STAGE 4b: REVISE COVER LETTER — Always regenerate cover letter
    # using the revised experience section. This ensures the cover letter
    # stays aligned with the CV after revision, even if the reviewer found
    # no specific cover letter issues. Falls back to original on LLM error.
    # ═══════════════════════════════════════════════════════════════

    try:
        revised_cover_letter = await _generate_cover_letter(
            candidate, job, evaluation, revised_experience, provider_config
        )
        logger.info("Cover letter regenerated with revised experience")
    except Exception as e:
        logger.warning(f"Cover letter revision failed — keeping original: {e}")
        revised_cover_letter = cover_letter_content

    # Persist revised stage
    application.pipeline_stage = "revised"
    await db.commit()

    # ═══════════════════════════════════════════════════════════════
    # STAGE 5: RENDER FINAL — Produce final LaTeX from revised content
    # ═══════════════════════════════════════════════════════════════

    final_cv_tex = render_cv_latex(candidate, revised_experience, job)
    final_cover_tex = render_cover_letter_latex(candidate, job, revised_cover_letter)

    # ═══════════════════════════════════════════════════════════════
    # STAGE 6: COMPILE — LaTeX compilation + page count verification
    # ═══════════════════════════════════════════════════════════════

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        fonts_dest = tmpdir_path / "OpenFonts"
        if OPENFONTS_DIR.exists():
            shutil.copytree(OPENFONTS_DIR, fonts_dest)

        if COVER_CLS.exists():
            shutil.copy2(COVER_CLS, tmpdir_path / "cover.cls")

        # ═══════════════════════════════════════════════════════════
        # STAGE 6a: COMPILE CV — with auto-trim if over page limit
        # ═══════════════════════════════════════════════════════════
        # First, try to compile with expected page count. If the CV
        # exceeds 2 pages, the CV cutter will remove the lowest-scoring
        # bullets iteratively until the CV fits (or all bullets are
        # protected at minimum 1 per entry).

        cv_trim_result = None
        cv_trimmed_experience = revised_experience
        cv_compile_success = False

        try:
            cv_pdf, cv_pages = await compile_latex(
                final_cv_tex, tmpdir_path,
                f"cv_{job.company}_{job.title}", "lualatex", 2,
            )
            cv_compile_success = True
        except LatexCompileError as e:
            # Check if the error is due to wrong page count (CV > 2)
            if "Wrong page count" in str(e) and "expected 2" in str(e):
                logger.info(
                    f"CV for {job.company}/{job.title} exceeds 2 pages — "
                    f"running relevance-weighted trim..."
                )
                try:
                    # Define a render wrapper that uses the still-available
                    # final_cover_tex for cover letter reference scoring
                    def _make_render_fn(candidate, job):
                        def _render(exp):
                            return render_cv_latex(candidate, exp, job)
                        return _render

                    # Define a compile wrapper that returns page count
                    # WITHOUT raising on wrong count.
                    # Uses compile_latex_get_pages() which always returns
                    # actual page count without checking expected_pages.
                    async def _compile_no_raise(tex, out_dir, name):
                        """Compile and return (path, pages) without raising on wrong count."""
                        return await compile_latex_get_pages(
                            tex, out_dir, name, "lualatex",
                        )

                    # Run the CV cutter
                    cv_trimmed_experience, cv_trim_result = await cv_cutter.trim_cv_to_page_limit(
                        experience=revised_experience,
                        job_posting=job,
                        cover_letter_latex=final_cover_tex,
                        render_fn=_make_render_fn(candidate, job),
                        compile_fn=_compile_no_raise,
                        output_dir=tmpdir_path,
                        job_name=f"cv_{job.company}_{job.title}",
                        max_pages=2,
                    )

                    # Re-render final LaTeX with trimmed experience
                    final_cv_tex = render_cv_latex(candidate, cv_trimmed_experience, job)

                    # Final compile
                    cv_pdf, cv_pages = await compile_latex(
                        final_cv_tex, tmpdir_path,
                        f"cv_{job.company}_{job.title}", "lualatex", 2,
                    )
                    cv_compile_success = True
                    logger.info(
                        f"CV trim successful: {cv_trim_result.bullets_removed} "
                        f"bullet(s) removed, final {cv_pages} page(s)."
                    )
                except LatexCompileError as trim_error:
                    # CV cutter couldn't get it to fit — re-raise the original
                    raise e from trim_error
            else:
                # Real compilation error — re-raise
                raise

        # Update revised_experience if trimming occurred
        if cv_trim_result and cv_trim_result.was_trimmed:
            revised_experience = cv_trimmed_experience

        # ═══════════════════════════════════════════════════════════
        # STAGE 6b: COMPILE COVER LETTER
        # ═══════════════════════════════════════════════════════════
        cover_pdf, cover_pages = await compile_latex(
            final_cover_tex, tmpdir_path,
            f"cover_{job.company}_{job.title}", "xelatex", 1,
        )

        generated_dir = Path("generated") / user_id / job_posting_id
        generated_dir.mkdir(parents=True, exist_ok=True)

        cv_pdf_final = generated_dir / f"cv_{job.company}_{job.title}.pdf"
        cover_pdf_final = generated_dir / f"cover_{job.company}_{job.title}.pdf"
        cv_tex_final = generated_dir / f"cv_{job.company}_{job.title}.tex"
        cover_tex_final = generated_dir / f"cover_{job.company}_{job.title}.tex"

        shutil.copy2(cv_pdf, cv_pdf_final)
        shutil.copy2(cover_pdf, cover_pdf_final)
        cv_tex_final.write_text(final_cv_tex, encoding="utf-8")
        cover_tex_final.write_text(final_cover_tex, encoding="utf-8")

        # ═══════════════════════════════════════════════════════════
        # STAGE 6c: COMPILATION VERIFICATION LOOP
        # ═══════════════════════════════════════════════════════════
        # Runs the iterative verification loop: checks for orphaned
        # \cventry entries, cover letter signature presence, and
        # applies \needspace{}/\enlargethispage{} fixes as needed.
        # Only runs if LaTeX compilation succeeded. Non-blocking:
        # if verification fails, the pipeline continues with warnings.

        if cv_compile_success:
            try:
                # Create compile wrappers that don't raise on page count
                from app.services.apply import compile_latex_get_pages

                async def _verify_compile_cv(tex, out_dir, name):
                    return await compile_latex_get_pages(
                        tex, out_dir, name, "lualatex"
                    )

                async def _verify_compile_cover(tex, out_dir, name):
                    return await compile_latex_get_pages(
                        tex, out_dir, name, "xelatex"
                    )

                # Optional ATS check function for the verification loop
                async def _verify_ats(pdf):
                    return await ats_check.check_ats_parseability(
                        pdf_path=pdf, job_posting=job, candidate=candidate
                    )

                verify_result = await pdf_compiler.compile_with_verification(
                    cv_latex=final_cv_tex,
                    cover_letter_latex=final_cover_tex,
                    cv_name=f"cv_{job.company}_{job.title}",
                    cover_name=f"cover_{job.company}_{job.title}",
                    candidate_name=candidate.full_name,
                    output_dir=tmpdir_path,
                    compile_cv_fn=_verify_compile_cv,
                    compile_cover_fn=_verify_compile_cover,
                    ats_check_fn=_verify_ats,
                )

                if verify_result.total_iterations > 1:
                    logger.info(
                        f"Compilation verification: {verify_result.total_iterations} "
                        f"iteration(s), {len(verify_result.final_issues)} "
                        f"remaining issue(s)."
                    )

                # If verification applied fixes and recompiled, update
                # the final generated files
                if verify_result.cv_pdf_path and str(verify_result.cv_pdf_path) != str(cv_pdf_final):
                    cv_pdf = Path(verify_result.cv_pdf_path)
                    if cv_pdf.exists():
                        shutil.copy2(cv_pdf, cv_pdf_final)
                if verify_result.cover_pdf_path and str(verify_result.cover_pdf_path) != str(cover_pdf_final):
                    cover_pdf = Path(verify_result.cover_pdf_path)
                    if cover_pdf.exists():
                        shutil.copy2(cover_pdf, cover_pdf_final)

                # Update final LaTeX if verification modified it
                if verify_result.cv_latex and verify_result.cv_latex != final_cv_tex:
                    final_cv_tex = verify_result.cv_latex
                    cv_tex_final.write_text(final_cv_tex, encoding="utf-8")
                if verify_result.cover_latex and verify_result.cover_latex != final_cover_tex:
                    final_cover_tex = verify_result.cover_latex
                    cover_tex_final.write_text(final_cover_tex, encoding="utf-8")

                # Log any unresolved issues
                for issue in verify_result.final_issues:
                    logger.warning(
                        f"Compilation issue ({issue.category.value}): {issue.description}"
                    )

            except Exception as e:
                # Compilation verification should never block the pipeline
                logger.warning(f"Compilation verification loop failed (non-blocking): {e}")

        # ═══════════════════════════════════════════════════════════
        # STAGE 7: ATS CHECK (inside tempdir, cv_pdf still exists)
        # ═══════════════════════════════════════════════════════════
        # Runs a 100% deterministic ATS parseability check on the
        # compiled CV PDF. Checks: CID markers, keyword coverage,
        # email/phone/name as extractable text, reading order.
        # Non-blocking — if pdftotext is not available, the check
        # is skipped gracefully. Must run INSIDE the temp directory
        # block because cv_pdf points to a temp file that will be
        # deleted when the block exits.
        ats_result = None
        try:
            ats_result = await ats_check.check_ats_parseability(
                pdf_path=cv_pdf,
                job_posting=job,
                candidate=candidate,
            )
            logger.info(
                f"ATS check for {job.company}/{job.title}: "
                f"pass={ats_result.pass_ats}, "
                f"keyword_coverage={ats_result.keyword_coverage:.0%}, "
                f"cid_markers={ats_result.has_cid_markers}, "
                f"email_found={ats_result.has_email}, "
                f"name_found={ats_result.has_candidate_name}"
            )
        except Exception as e:
            # ATS check should never block the pipeline
            logger.warning(f"ATS check failed (non-blocking): {e}")

    # 8. Extract incorporated keywords and addressed red flags
    incorporated_keywords = _extract_incorporated_keywords(revised_experience, evaluation.missing_keywords or [])
    addressed_red_flags = _extract_addressed_red_flags(revised_experience, evaluation.red_flags or [])

    # 9. Update Application record with final generated content
    application.tailored_experience = [exp.model_dump() for exp in revised_experience]
    application.incorporated_keywords = [k.model_dump() for k in incorporated_keywords]
    application.addressed_red_flags = [r.model_dump() for r in addressed_red_flags]
    application.cv_tex_path = str(cv_tex_final)
    application.cv_pdf_path = str(cv_pdf_final)
    application.cover_letter_tex_path = str(cover_tex_final)
    application.cover_letter_pdf_path = str(cover_pdf_final)
    application.cv_compiled = True
    application.cv_pages = cv_pages
    application.cover_letter_compiled = True
    application.cover_letter_pages = cover_pages
    application.pipeline_stage = "verified" if (ats_result and ats_result.pass_ats) else "compiled"
    application.ats_score = ats_result.keyword_coverage if ats_result else None
    application.ats_missing_keywords = ats_result.missing_keywords if ats_result else None
    application.ats_pass = ats_result.pass_ats if ats_result else None
    application.ats_checked_at = datetime.now(timezone.utc) if ats_result else None

    # 10. Update job posting status
    job.status = "applied"

    await db.commit()
    await db.refresh(application)

    # Build a comprehensive result message including review + ATS insights
    issues_summary = ""
    if review_feedback.issues:
        high_severity = [i for i in review_feedback.issues if i.severity == "high"]
        medium_severity = [i for i in review_feedback.issues if i.severity == "medium"]
        if high_severity:
            issues_summary = f" {len(high_severity)} high-severity, "
            issues_summary += f"{len(medium_severity)} medium-severity issues found and addressed."
        else:
            issues_summary = f" {len(review_feedback.issues)} issue(s) addressed."

    ats_summary = ""
    if ats_result is not None:
        if ats_result.pass_ats:
            ats_summary = f" ATS check passed ({ats_result.keyword_coverage:.0%} keyword coverage)."
        else:
            ats_summary = f" ATS check flagged {len(ats_result.missing_keywords)} missing keywords."

    final_stage = "compiled"
    if ats_result and ats_result.pass_ats:
        final_stage = "verified"

    return ApplyResult(
        application_id=application.id,
        cv_compiled=True,
        cv_pages=cv_pages,
        cover_letter_compiled=True,
        cover_letter_pages=cover_pages,
        message=f"Application generated with Drafter-Reviewer pipeline: "
                f"CV ({cv_pages} pages), Cover Letter ({cover_pages} page)."
                f"{issues_summary}{ats_summary}"
                f" Pipeline stages: draft → reviewed → revised → compiled → ats_check.",
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
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        try:
            result = await db.execute(
                select(Application).where(Application.id == application_id)
            )
            application = result.scalar_one_or_none()
            if application is None:
                logger.error("execute_apply_background: Application %s not found", application_id)
                return

            application.pipeline_stage = "initializing"
            await db.commit()

            await execute_apply(
                db=db,
                user_id=application.user_id,
                job_posting_id=application.job_posting_id,
                provider_config=provider_config,
                cv_template=application.cv_template,
                cover_letter_template=application.cover_letter_template,
                application=application,
            )
        except Exception as e:
            logger.error("Pipeline failed for application %s: %s", application_id, e)
            try:
                await db.rollback()
                application.pipeline_stage = "failed"
                await db.commit()
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


async def _generate_tailored_experience(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
    provider_config: dict | None = None,
) -> list[TailoredExperienceEntry]:
    """Call LLM to generate tailored experience section."""
    messages = build_tailored_experience_prompt(candidate, job, evaluation)

    try:
        provider_kwargs = _get_provider_kwargs(provider_config)
        result: TailoredExperienceLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=TailoredExperienceLLMOutput,
            **provider_kwargs,
            temperature=0.3,
            max_tokens=3000,
        )
        return result.tailored_experience
    except Exception as e:
        raise LLMError(f"Failed to generate tailored experience: {e}") from e


async def _generate_cover_letter(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation,
    tailored_experience: list[TailoredExperienceEntry],
    provider_config: dict | None = None,
) -> CoverLetterLLMOutput:
    """Call LLM to generate cover letter content."""
    messages = build_cover_letter_prompt(candidate, job, evaluation, tailored_experience)

    try:
        provider_kwargs = _get_provider_kwargs(provider_config)
        result: CoverLetterLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=CoverLetterLLMOutput,
            **provider_kwargs,
            temperature=0.4,
            max_tokens=2000,
        )
        return result
    except Exception as e:
        raise LLMError(f"Failed to generate cover letter: {e}") from e


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
