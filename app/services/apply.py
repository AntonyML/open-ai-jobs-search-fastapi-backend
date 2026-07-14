"""Apply service — generates tailored CV and cover letter.

Implements the /apply workflow from the original repo:
1. Takes rank evaluation (missing keywords, red flags)
2. Rewrites experience section using X-Y-Z formula (Google style)
4. Incorporates missing keywords ONLY where true for candidate's real experience
5. Generates cover letter matching job posting language
6. Renders LaTeX templates (lualatex for CV, xelatex for cover letter)
7. Verifies page counts (CV=2 pages, cover letter=1 page)
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import Application, CandidateProfile, JobPosting, RankEvaluation, User
from app.exceptions import LLMError, LatexCompileError, NotFoundError, ProfileIncompleteError
from app.llm.adapter import llm_completion_structured, get_provider_kwargs
from app.schemas.apply import (
    AddressedRedFlag,
    ApplyResult,
    CoverLetterLLMOutput,
    IncorporatedKeyword,
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
        return Path(settings.latex_bin_dir) / f"{name}.exe"
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

    # Format tailored experience for the prompt
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


def _build_candidate_summary_for_apply(candidate: CandidateProfile) -> str:
    """Build candidate summary for apply prompts."""
    parts = []

    if candidate.full_name:
        parts.append(f"Name: {candidate.full_name}")
    if candidate.location:
        parts.append(f"Location: {candidate.location}")
    if candidate.employment_status:
        parts.append(f"Status: {candidate.employment_status}")

    # Education
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

    # Experience (full for reference)
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

    # Skills
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

    # Read master template
    template = CV_MASTER_TEMPLATE.read_text(encoding="utf-8")

    # Replace placeholders
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

    # Replace profile statement
    profile_stmt = candidate.profile_statement or "Experienced professional seeking new opportunities."
    template = re.sub(
        r"\\small\{.*?Profile statement.*?\}",
        lambda m: f"\\small{{{profile_stmt}}}",
        template,
        flags=re.DOTALL,
    )

    # Replace Core Competencies
    skills_section = _build_skills_section(candidate)
    template = re.sub(
        r"\\section\{Core Competencies\}.*?\\section\{Professional Experience\}",
        lambda m: f"\\section{{Core Competencies}}\n\\vspace{{1pt}}\n{skills_section}\n\n\\section{{Professional Experience}}",
        template,
        flags=re.DOTALL,
    )

    # Replace Professional Experience
    exp_section = _build_experience_section(tailored_experience)
    template = re.sub(
        r"\\section\{Professional Experience\}.*?\\section\{Education\}",
        lambda m: f"\\section{{Professional Experience}}\n\\vspace{{3pt}}\n{exp_section}\n\n\\section{{Education}}",
        template,
        flags=re.DOTALL,
    )

    # Replace Education
    edu_section = _build_education_section(candidate)
    template = re.sub(
        r"\\section\{Education\}.*?\\section\{Selected Publications\}",
        lambda m: f"\\section{{Education}}\n\\vspace{{3pt}}\n{edu_section}\n\n\\section{{Selected Publications}}",
        template,
        flags=re.DOTALL,
    )

    # Replace Publications
    pub_section = _build_publications_section(candidate)
    template = re.sub(
        r"\\section\{Selected Publications\}.*?\\section\{Honors and Awards\}",
        lambda m: f"\\section{{Selected Publications}}\n\\vspace{{3pt}}\n{pub_section}\n\n\\section{{Honors and Awards}}",
        template,
        flags=re.DOTALL,
    )

    # Replace Awards
    awards_section = _build_awards_section(candidate)
    template = re.sub(
        r"\\section\{Honors and Awards\}.*?\\section\{References\}",
        lambda m: f"\\section{{Honors and Awards}}\n\\vspace{{3pt}}\n{awards_section}\n\n\\section{{References}}",
        template,
        flags=re.DOTALL,
    )

    # Replace References
    ref_section = _build_references_section(candidate)
    template = re.sub(
        r"\\section\{References\}.*?\\end\{document\}",
        lambda m: f"\\section{{References}}\n\\vspace{{3pt}}\n{ref_section}\n\n\\end{{document}}",
        template,
        flags=re.DOTALL,
    )

    return template


def _build_skills_section(candidate: CandidateProfile) -> str:
    """Build the Core Competencies itemize section."""
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
    """Build the Professional Experience section with tailored entries."""
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
    """Build the Education section."""
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
    """Build the Publications section."""
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
    """Build the Awards section."""
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
    """Build the References section."""
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

    # Replace placeholders
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

    # Handle bullet list - needs to be outside \lettercontent{} with Raleway-Medium font
    bullets = cover_letter_content.body_paragraphs[1:] if len(cover_letter_content.body_paragraphs) > 1 else []
    if bullets:
        bullet_tex = _build_cover_letter_bullets(bullets)
        # Find the position after the first body paragraph and insert bullets
        template = _insert_cover_letter_bullets(template, bullet_tex)

    return template


def _build_cover_letter_bullets(bullets: list[str]) -> str:
    """Build the bullet list for cover letter with correct font."""
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
    """Insert bullet list after the first body paragraph in cover letter."""
    # Find the first body paragraph end and insert bullets after it
    # The template has: \lettercontent{[Body paragraph...]} then we need to insert bullets
    # then \lettercontent{[Connection paragraph...]}
    # Simple approach: replace the pattern
    pattern = r"(\\lettercontent\{[^}]*\})(\s*)(\\lettercontent\{)"
    return re.sub(
        pattern,
        lambda m: m.group(1) + m.group(2) + bullet_tex + m.group(2) + m.group(3),
        template,
        count=1,
    )


# ── LaTeX compilation ───────────────────────────────────────────────


async def compile_latex(
    tex_content: str,
    output_dir: Path,
    job_name: str,
    engine: str = "lualatex",
    expected_pages: int = 2,
) -> tuple[Path, int]:
    """Compile LaTeX to PDF and verify page count.

    Args:
        tex_content: The .tex file content
        output_dir: Directory to write output
        job_name: Base name for output files (without extension)
        engine: 'lualatex' or 'xelatex'
        expected_pages: Expected page count (2 for CV, 1 for cover letter)

    Returns:
        Tuple of (pdf_path, actual_page_count)

    Raises:
        LatexCompileError: If compilation fails or page count is wrong
    """
    tex_file = output_dir / f"{job_name}.tex"
    pdf_file = output_dir / f"{job_name}.pdf"

    # Write .tex file
    tex_file.write_text(tex_content, encoding="utf-8")

    # Run LaTeX (twice for references)
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

    # Verify PDF exists
    if not pdf_file.exists():
        raise LatexCompileError(f"PDF not generated: {pdf_file}")

    # Check page count using pdftotext or pdfinfo
    actual_pages = await _get_pdf_page_count(pdf_file)
    if actual_pages != expected_pages:
        raise LatexCompileError(
            f"Wrong page count for {job_name}: expected {expected_pages}, got {actual_pages}"
        )

    return pdf_file, actual_pages


async def _get_pdf_page_count(pdf_path: Path) -> int:
    """Get page count of a PDF using pdftotext or pdfinfo."""
    # Try pdfinfo first (poppler-utils)
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
                # pdfinfo outputs "Pages: N"
                match = re.search(r"Pages:\s+(\d+)", output)
                if match:
                    return int(match.group(1))
                # pdftotext to stdout - count form feeds
                return output.count("\f") + 1
        except FileNotFoundError:
            continue

    # Fallback: assume 1 page if we can't determine
    return 1


# ── Main orchestration ──────────────────────────────────────────────


async def execute_apply(
    db: AsyncSession,
    user_id: str,
    job_posting_id: str,
    rank_evaluation_id: str | None = None,
    cv_template: str = "moderncv-banking",
    cover_letter_template: str = "cover-cls",
    provider_config: dict | None = None,
) -> ApplyResult:
    """Execute the full apply workflow.

    Args:
        db: Database session
        user_id: Authenticated user ID
        job_posting_id: Job to apply to
        rank_evaluation_id: Specific evaluation to use (optional)
        cv_template: CV template name
        cover_letter_template: Cover letter template name
        provider_config: Optional LLM provider configuration

    Returns:
        ApplyResult with application ID and compilation status
    """
    # 1. Get job posting
    job_result = await db.execute(
        select(JobPosting).where(
            JobPosting.id == job_posting_id,
            JobPosting.user_id == user_id,
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job posting not found.")

    # 2. Get rank evaluation
    if rank_evaluation_id:
        eval_result = await db.execute(
            select(RankEvaluation).where(
                RankEvaluation.id == rank_evaluation_id,
                RankEvaluation.job_posting_id == job_posting_id,
            )
        )
    else:
        # Use the latest evaluation for this job
        eval_result = await db.execute(
            select(RankEvaluation)
            .where(RankEvaluation.job_posting_id == job_posting_id)
            .order_by(RankEvaluation.created_at.desc())
        )
    evaluation = eval_result.scalar_one_or_none()
    if evaluation is None:
        raise NotFoundError("Rank evaluation not found. Run /rank first.")

    # 3. Get candidate profile
    candidate_result = await db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user_id)
    )
    candidate = candidate_result.scalar_one_or_none()
    if candidate is None:
        raise ProfileIncompleteError("Candidate profile not found. Run /setup first.")

    # 4. Generate tailored experience via LLM
    tailored_experience = await _generate_tailored_experience(candidate, job, evaluation, provider_config)

    # 5. Generate cover letter content via LLM
    cover_letter_content = await _generate_cover_letter(candidate, job, evaluation, tailored_experience, provider_config)

    # 6. Render LaTeX
    cv_tex = render_cv_latex(candidate, tailored_experience, job)
    cover_tex = render_cover_letter_latex(candidate, job, cover_letter_content)

    # 7. Compile PDFs in temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Copy font directory for cover letter
        fonts_dest = tmpdir_path / "OpenFonts"
        if OPENFONTS_DIR.exists():
            shutil.copytree(OPENFONTS_DIR, fonts_dest)

        # Copy cover.cls
        if COVER_CLS.exists():
            shutil.copy2(COVER_CLS, tmpdir_path / "cover.cls")

        # Compile CV (lualatex, 2 pages)
        cv_pdf, cv_pages = await compile_latex(
            cv_tex, tmpdir_path, f"cv_{job.company}_{job.title}", "lualatex", 2
        )

        # Compile Cover Letter (xelatex, 1 page)
        cover_pdf, cover_pages = await compile_latex(
            cover_tex, tmpdir_path, f"cover_{job.company}_{job.title}", "xelatex", 1
        )

        # Copy PDFs to permanent storage (in a real app, you'd upload to S3/Supabase Storage)
        # For now, we'll store paths relative to a generated directory
        generated_dir = Path("generated") / user_id / job_posting_id
        generated_dir.mkdir(parents=True, exist_ok=True)

        cv_pdf_final = generated_dir / f"cv_{job.company}_{job.title}.pdf"
        cover_pdf_final = generated_dir / f"cover_{job.company}_{job.title}.pdf"
        cv_tex_final = generated_dir / f"cv_{job.company}_{job.title}.tex"
        cover_tex_final = generated_dir / f"cover_{job.company}_{job.title}.tex"

        shutil.copy2(cv_pdf, cv_pdf_final)
        shutil.copy2(cover_pdf, cover_pdf_final)
        cv_tex_final.write_text(cv_tex, encoding="utf-8")
        cover_tex_final.write_text(cover_tex, encoding="utf-8")

    # 8. Extract incorporated keywords and addressed red flags from LLM outputs
    incorporated_keywords = _extract_incorporated_keywords(tailored_experience, evaluation.missing_keywords or [])
    addressed_red_flags = _extract_addressed_red_flags(tailored_experience, evaluation.red_flags or [])

    # 9. Create Application record
    application = Application(
        user_id=user_id,
        job_posting_id=job_posting_id,
        rank_evaluation_id=evaluation.id,
        tailored_experience=[exp.model_dump() for exp in tailored_experience],
        incorporated_keywords=[k.model_dump() for k in incorporated_keywords],
        addressed_red_flags=[r.model_dump() for r in addressed_red_flags],
        cv_tex_path=str(cv_tex_final),
        cv_pdf_path=str(cv_pdf_final),
        cover_letter_tex_path=str(cover_tex_final),
        cover_letter_pdf_path=str(cover_pdf_final),
        cv_compiled=True,
        cv_pages=cv_pages,
        cover_letter_compiled=True,
        cover_letter_pages=cover_pages,
        cv_template=cv_template,
        cover_letter_template=cover_letter_template,
        language=job.language or "en",
    )
    db.add(application)

    # 10. Update job posting status
    job.status = "applied"

    await db.commit()
    await db.refresh(application)

    return ApplyResult(
        application_id=application.id,
        cv_compiled=True,
        cv_pages=cv_pages,
        cover_letter_compiled=True,
        cover_letter_pages=cover_pages,
        message=f"Application generated: CV ({cv_pages} pages), Cover Letter ({cover_pages} page)",
    )


def _get_provider_kwargs(provider_config: dict | None) -> dict:
    """Extract provider kwargs from provider config for LLM calls.

    Args:
        provider_config: Dict with provider, model, api_key, api_base

    Returns:
        Dict with provider, model, api_key, api_base for LLM calls
    """
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
        return result.experience
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
            # Find which bullet contains it
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
        # Simple heuristic: if flag keywords appear in tailored experience
        flag_keywords = flag.lower().split()
        if any(kw in all_text for kw in flag_keywords if len(kw) > 3):
            addressed.append(
                AddressedRedFlag(
                    red_flag=flag,
                    how_addressed=f"Reframed in tailored experience bullets",
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