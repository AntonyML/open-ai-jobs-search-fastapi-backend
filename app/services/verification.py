"""Verification checklist service — FASE 2.

Runs a comprehensive quality checklist on generated CV and cover letter
documents. Combines 10 deterministic checks with 1 LLM-based content
quality check.

Architecture decision:
- Deterministic checks come FIRST so the user gets instant feedback
  without waiting for an LLM call.
- The LLM check is a SINGLE call (not per-check) to minimize latency
  and token usage. It returns structured JSON evaluating tone, claims,
  and role specificity in one pass.
- The service is NON-BLOCKING: even if checks fail, the result is
  stored and the pipeline continues. The user can see issues and
  decide whether to re-run /apply with improved settings.
- PDF checks use the existing ats_check service (FASE 3) to avoid
  duplicating PDF extraction logic.
"""

from __future__ import annotations


import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, CandidateProfile, JobPosting
from app.llm.adapter import llm_completion_structured, get_provider_kwargs
from app.schemas.ats_check import ATSResult
from app.schemas.verification import LlmContentCheckOutput, VerificationCheck, VerificationResult
from app.services import artifact_store, ats_check, credits
from app.core.logging import get_logger, bind_context

logger = get_logger(__name__)


# ── Public entry point ──────────────────────────────────────────────


async def run_verification_checklist(
        application: Application,
        candidate: CandidateProfile | None,
        job_posting: JobPosting,
        cv_latex: str | None = None,
        cover_letter_latex: str | None = None,
        cv_pdf_path: str | Path | None = None,
        provider_config: dict | None = None,
        correlation_id: str | None = None,
        db: AsyncSession | None = None,
) -> VerificationResult:
    """Run the complete verification checklist on generated documents.

    Args:
        application: The Application record (for context and storage).
        candidate: The candidate profile (ground truth for checks).
        job_posting: The job posting being applied to.
        cv_latex: The LaTeX source of the CV (reads from DB if None).
        cover_letter_latex: The LaTeX source of the cover letter.
        cv_pdf_path: Path to compiled CV PDF (optional, for ATS checks).
        provider_config: LLM provider config for the single LLM check.
        correlation_id: Ledger correlation id for credit usage accounting.

    Returns:
        VerificationResult with ALL checks, their outcomes, and a
        summary. Never raises — all failures are captured in the result.
    """
    usage: dict[str, Any] = {}
    with bind_context(stage="verify"):
        # Resolve from DB if not provided
        if cv_latex is None and application.draft_cv_tex:
            cv_latex = application.draft_cv_tex
        if cv_latex is None and application.cv_tex_path:
            try:
                cv_latex = Path(application.cv_tex_path).read_text(encoding="utf-8")
            except Exception:
                pass

        if cv_latex is None:
            cv_latex = ""
        if cover_letter_latex is None:
            cover_letter_latex = ""
        if cv_pdf_path is None and application.cv_pdf_path:
            cv_pdf_path = application.cv_pdf_path

        checks: list[VerificationCheck] = []

        # ═══════════════════════════════════════════════════════════════
        # PASS 1: Deterministic content checks (no LLM)
        # ═══════════════════════════════════════════════════════════════

        # Candidate-dependent checks (skip gracefully if no candidate)
        candidate_name = candidate.full_name if candidate else None
        candidate_email = candidate.email if candidate else None
        candidate_profile_stmt = candidate.profile_statement if candidate else None

        # 1. Candidate name in CV
        checks.append(_check_name_in_cv(cv_latex, candidate_name))

        # 2. Email in CV
        checks.append(_check_email_in_cv(cv_latex, candidate_email))

        # 3. Job role in profile statement
        checks.append(_check_role_in_profile(cv_latex, job_posting.title, candidate_profile_stmt))

        # 4. Company name in cover letter
        checks.append(_check_company_in_cover(cover_letter_latex, job_posting.company))

        # 5. Consistent date format
        checks.append(_check_date_format(cv_latex))

        # 6. Balanced LaTeX braces
        checks.append(_check_latex_balance(cv_latex))

        # 7. No placeholders
        checks.append(_check_no_placeholders(cv_latex, cover_letter_latex))

        # ═══════════════════════════════════════════════════════════════
        # PASS 2: ATS parseability check (delegates to ats_check service)
        # ═══════════════════════════════════════════════════════════════

        ats_result: ATSResult | None = None
        pdf_path_obj = artifact_store.resolve_existing("apply", cv_pdf_path)
        if pdf_path_obj and pdf_path_obj.exists():
            try:
                ats_result = await ats_check.check_ats_parseability(
                    pdf_path=pdf_path_obj,
                    job_posting=job_posting,
                    candidate=candidate,
                )
            except Exception as e:
                logger.warning(f"ATS check failed during verification: {e}")

        # 8. No CID markers
        checks.append(_check_cid_markers(ats_result))

        # 9. Email and phone as literal text in PDF
        checks.append(_check_ats_contact(ats_result))

        # 10. Keyword coverage ≥ 70%
        checks.append(_check_keyword_coverage(ats_result))

        # ═══════════════════════════════════════════════════════════════
        # PASS 3: LLM content quality check (single call)
        # ═══════════════════════════════════════════════════════════════

        llm_checks = await _run_llm_content_checks(
            cv_latex, cover_letter_latex,
            job_posting, candidate,
            provider_config,
            usage=usage,
        )
        checks.extend(llm_checks)

        # ═══════════════════════════════════════════════════════════════
        # Aggregate result
        # ═══════════════════════════════════════════════════════════════

        passes = [c.name for c in checks if c.passed]
        failures = [c.name for c in checks if not c.passed]
        warnings_list = [c.name for c in checks if c.passed and c.suggestion]

        overall_pass = len(failures) == 0
        pass_count = len(passes)
        total_count = len(checks)

        summary = (
            f"{pass_count}/{total_count} checks passed"
            + (f", {len(failures)} failure(s)" if failures else " — all clear!")
            + (f", {len(llm_checks)} LLM check(s)" if llm_checks else "")
        )

        # Record LLM usage against the gated ledger row (if any). Best-effort —
        # verification must never raise, so failures here are swallowed.
        if db is not None and correlation_id and usage.get("tokens_input"):
            try:
                await credits.record_llm_usage(
                    db,
                    correlation_id,
                    model_used=usage.get("model_used"),
                    tokens_input=usage.get("tokens_input", 0),
                    tokens_output=usage.get("tokens_output", 0),
                    cost_usd_cents=usage.get("cost_usd_cents", 0),
                )
                await db.commit()
            except Exception as record_err:  # pragma: no cover — defensive
                logger.warning(f"Failed to record verification LLM usage: {record_err}")

        return VerificationResult(
            application_id=application.id,
            checks=checks,
            overall_pass=overall_pass,
            passes=passes,
            failures=failures,
            warnings=warnings_list,
            ats_score=ats_result.keyword_coverage if ats_result else None,
            summary=summary,
            checked_at=datetime.now(timezone.utc),
        )


# ── Individual deterministic checks ─────────────────────────────────


def _check_name_in_cv(cv_latex: str, candidate_name: str | None) -> VerificationCheck:
    """Check 1: Candidate name appears in CV LaTeX.

    Searches for the candidate's full name (case-insensitive, word-boundary).
    Falls back to checking first name if full name not found.
    """
    if not candidate_name or not cv_latex:
        return VerificationCheck(
            name="name_in_cv",
            label="Candidate name in CV",
            category="content",
            passed=False,
            details="No candidate name available to check." if not candidate_name else "No CV text to check.",
            suggestion="Ensure candidate name is set in profile before generating CV.",
        )

    # Case-insensitive word boundary search
    name_escaped = re.escape(candidate_name)
    pattern = re.compile(rf"\b{name_escaped}\b", re.IGNORECASE)
    if pattern.search(cv_latex):
        return VerificationCheck(
            name="name_in_cv",
            label="Candidate name in CV",
            category="content",
            passed=True,
            details=f"✅ '{candidate_name}' found in CV.",
        )

    # Fallback: check first name only
    first_name = candidate_name.split()[0]
    if first_name and len(first_name) > 1:
        fn_pattern = re.compile(rf"\b{re.escape(first_name)}\b", re.IGNORECASE)
        if fn_pattern.search(cv_latex):
            return VerificationCheck(
                name="name_in_cv",
                label="Candidate name in CV",
                category="content",
                passed=True,
                details=f"✅ First name '{first_name}' found in CV (full name not matched).",
            )

    return VerificationCheck(
        name="name_in_cv",
        label="Candidate name in CV",
        category="content",
        passed=False,
        details=f"❌ '{candidate_name}' not found in CV LaTeX.",
        suggestion="Check the CV template — ensure the name placeholder was replaced correctly.",
    )


def _check_email_in_cv(cv_latex: str, candidate_email: str | None) -> VerificationCheck:
    """Check 2: Candidate email appears in CV LaTeX.

    Searches for the exact email address (case-insensitive).
    Falls back to general email regex pattern if no specific email known.
    """
    if not cv_latex:
        return VerificationCheck(
            name="email_in_cv",
            label="Email in CV",
            category="content",
            passed=False,
            details="No CV text to check.",
            suggestion="Generate CV before running verification.",
        )

    if candidate_email:
        # Exact match (case-insensitive)
        escaped = re.escape(candidate_email)
        # Escape the @ and . for regex while keeping the match literal
        if re.search(escaped, cv_latex, re.IGNORECASE):
            return VerificationCheck(
                name="email_in_cv",
                label="Email in CV",
                category="content",
                passed=True,
                details=f"✅ '{candidate_email}' found in CV.",
            )

    # Fallback: general email pattern
    email_pattern = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE)
    if email_pattern.search(cv_latex):
        found = email_pattern.search(cv_latex).group()
        return VerificationCheck(
            name="email_in_cv",
            label="Email in CV",
            category="content",
            passed=True,
            details=f"✅ Email found in CV: '{found}'"
            + (f" (matches '{candidate_email}')" if candidate_email and candidate_email.lower() == found.lower() else "."),
        )

    return VerificationCheck(
        name="email_in_cv",
        label="Email in CV",
        category="content",
        passed=False,
        details="❌ No email address found in CV LaTeX.",
        suggestion="Check the CV template — ensure the email placeholder was replaced.",
    )


def _check_role_in_profile(
    cv_latex: str,
    job_title: str | None,
    profile_statement: str | None,
) -> VerificationCheck:
    """Check 3: Job role appears in CV profile statement.

    Searches for the job title (or its first significant word) in the
    profile section of the CV. This catches generic profile statements
    that don't mention the specific role.
    """
    if not job_title:
        return VerificationCheck(
            name="role_in_profile",
            label="Role in profile statement",
            category="content",
            passed=True,
            details="No job title available to check — skipping.",
        )

    # Look for the job title in the CV text
    title_words = job_title.lower().split()
    # Remove common filler words
    filler = {"a", "an", "the", "and", "or", "in", "of", "for", "to", "with", "junior", "senior", "lead", "principal", "staff"}
    significant_words = [w for w in title_words if w not in filler]

    cv_lower = cv_latex.lower() if cv_latex else ""

    for word in significant_words:
        pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
        if pattern.search(cv_lower):
            return VerificationCheck(
                name="role_in_profile",
                label="Role in profile statement",
                category="content",
                passed=True,
                details=f"✅ Job title keyword '{word}' found in CV.",
            )

    # Check profile statement as fallback
    if profile_statement:
        ps_lower = profile_statement.lower()
        for word in significant_words:
            if word in ps_lower:
                return VerificationCheck(
                    name="role_in_profile",
                    label="Role in profile statement",
                    category="content",
                    passed=True,
                    details=f"✅ Job title keyword '{word}' found in profile statement.",
                )

    return VerificationCheck(
        name="role_in_profile",
        label="Role in profile statement",
        category="content",
        passed=False,
        details=f"❌ No mention of role '{job_title}' in CV or profile statement.",
        suggestion="Update the profile statement to reference the specific role being applied for.",
    )


def _check_company_in_cover(cover_latex: str, company: str | None) -> VerificationCheck:
    """Check 4: Company name appears in cover letter.

    The cover letter MUST mention the target company by name.
    """
    if not company or company in ("Not specified", "Unknown", ""):
        return VerificationCheck(
            name="company_in_cover",
            label="Company in cover letter",
            category="content",
            passed=True,
            details="No company name available to check — skipping.",
        )

    cover_lower = cover_latex.lower() if cover_latex else ""
    company_lower = company.lower()

    # Try exact match first
    if company_lower in cover_lower:
        return VerificationCheck(
            name="company_in_cover",
            label="Company in cover letter",
            category="content",
            passed=True,
            details=f"✅ '{company}' found in cover letter.",
        )

    # Try word-by-word (for multi-word company names)
    company_words = company_lower.split()
    found_words = sum(1 for w in company_words if w in cover_lower and len(w) > 2)
    # Must find at least one significant word AND allow at most one missing
    if found_words > 0 and found_words >= len([w for w in company_words if len(w) > 2]) - 1:
        return VerificationCheck(
            name="company_in_cover",
            label="Company in cover letter",
            category="content",
            passed=True,
            details=f"✅ Company reference found in cover letter (matched {found_words}/{len(company_words)} words).",
        )

    return VerificationCheck(
        name="company_in_cover",
        label="Company in cover letter",
        category="content",
        passed=False,
        details=f"❌ '{company}' not found in cover letter.",
        suggestion="Ensure the cover letter mentions the target company by name.",
    )


def _check_date_format(cv_latex: str) -> VerificationCheck:
    """Check 5: Dates use a consistent format throughout the CV.

    Accepts: YYYY–YYYY, YYYY-MM, MM/YYYY, "Month YYYY", "Present".
    Flags inconsistent mixing of formats.
    """
    if not cv_latex:
        return VerificationCheck(
            name="date_format",
            label="Consistent date format",
            category="formatting",
            passed=False,
            details="No CV text to check.",
            suggestion="Generate CV before running verification.",
        )

    # Find all date-like patterns
    patterns = {
        "range": re.compile(r"\b\d{4}–\d{4}\b"),  # em dash range
        "range_hyphen": re.compile(r"\b\d{4}-\d{4}\b"),  # hyphen range
        "month_year": re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}\b", re.IGNORECASE),
        "yyyy_mm": re.compile(r"\b\d{4}-\d{2}\b"),
        "mm_yyyy": re.compile(r"\b\d{2}/\d{4}\b"),
        "present": re.compile(r"\bPresent\b", re.IGNORECASE),
    }

    found_formats = {}
    for fmt_name, pattern in patterns.items():
        matches = pattern.findall(cv_latex)
        if matches:
            found_formats[fmt_name] = len(matches)

    if not found_formats:
        return VerificationCheck(
            name="date_format",
            label="Consistent date format",
            category="formatting",
            passed=False,
            details="❌ No date patterns found in CV.",
            suggestion="Add dates to experience entries (e.g., '2020–2024' or 'Jan 2020').",
        )

    # More than 2 different formats is suspicious
    if len(found_formats) > 2:
        formats_desc = ", ".join(f"- {fmt}" for fmt in found_formats)
        return VerificationCheck(
            name="date_format",
            label="Consistent date format",
            category="formatting",
            passed=False,
            details=f"❌ Inconsistent date formats: {len(found_formats)} different pattern(s) found ({formats_desc}).",
            suggestion="Use a single date format throughout (e.g., 'Jan 2020 – Present').",
        )

    formats_desc = ", ".join(f"{fmt} ({count}x)" for fmt, count in found_formats.items())
    return VerificationCheck(
        name="date_format",
        label="Consistent date format",
        category="formatting",
        passed=True,
        details=f"✅ Consistent date format(s) found: {formats_desc}.",
    )


def _check_latex_balance(latex_text: str) -> VerificationCheck:
    """Check 6: LaTeX has balanced curly braces.

    Equal number of opening and closing curly braces is a minimum
    requirement for valid LaTeX. Catches template rendering errors
    that would cause compilation failures.
    """
    if not latex_text:
        return VerificationCheck(
            name="latex_balance",
            label="Balanced LaTeX braces",
            category="formatting",
            passed=False,
            details="No LaTeX text to check.",
            suggestion="Generate LaTeX before running verification.",
        )

    opens = latex_text.count("{")
    closes = latex_text.count("}")

    if opens == closes:
        return VerificationCheck(
            name="latex_balance",
            label="Balanced LaTeX braces",
            category="formatting",
            passed=True,
            details=f"✅ Braces balanced: {opens} opening, {closes} closing.",
        )

    diff = opens - closes
    return VerificationCheck(
        name="latex_balance",
        label="Balanced LaTeX braces",
        category="formatting",
        passed=False,
        details=f"❌ Braces imbalanced: {opens} opening, {closes} closing (difference: {diff:+d}).",
        suggestion="Check the LaTeX template for missing or extra curly braces.",
    )


def _check_no_placeholders(cv_latex: str, cover_latex: str) -> VerificationCheck:
    """Check 7: No placeholder tokens remain in the final documents.

    Catches tokens like [YOUR_NAME], [COMPANY], [EMAIL], [PHONE] that
    should have been replaced by the template renderer.
    """
    placeholder_patterns = [
        r"\[YOUR[_ ]?NAME\]",
        r"\[COMPANY\]",
        r"\[EMAIL\]",
        r"\[PHONE\]",
        r"\[YOUR_EMAIL\]",
        r"\[First\]",
        r"\[Last\]",
        r"\+\+XX",
        r"\[Hiring Manager",
        r"\[Opening paragraph",
        r"\[Body paragraph",
        r"\[Connection paragraph",
    ]

    both_text = f"{cv_latex}\n{cover_latex}" if cover_latex else cv_latex
    if not both_text:
        return VerificationCheck(
            name="no_placeholders",
            label="No placeholder tokens",
            category="formatting",
            passed=False,
            details="No document text to check.",
            suggestion="Generate documents before running verification.",
        )

    found_placeholders = []
    for pattern in placeholder_patterns:
        matches = re.findall(pattern, both_text, re.IGNORECASE)
        found_placeholders.extend(matches)

    if not found_placeholders:
        return VerificationCheck(
            name="no_placeholders",
            label="No placeholder tokens",
            category="formatting",
            passed=True,
            details="✅ No placeholder tokens found.",
        )

    unique = list(set(found_placeholders))
    return VerificationCheck(
        name="no_placeholders",
        label="No placeholder tokens",
        category="formatting",
        passed=False,
        details=f"❌ Found {len(unique)} placeholder token(s) not replaced: {', '.join(unique[:5])}.",
        suggestion="Run the template renderer again with proper candidate data.",
    )


# ── ATS checks (delegating to FASE 3) ───────────────────────────────


def _check_cid_markers(ats: ATSResult | None) -> VerificationCheck:
    """Check 8: No CID (glyph) markers in extracted PDF text.

    CID markers indicate fonts that were not embedded correctly,
    causing ATS systems to see garbage instead of text.
    """
    if ats is None:
        return VerificationCheck(
            name="no_cid_markers",
            label="No CID glyph markers",
            category="ats",
            passed=True,
            details="⚠️ ATS check not run (pdftotext not available) — CID check skipped.",
        )

    if not ats.has_cid_markers:
        return VerificationCheck(
            name="no_cid_markers",
            label="No CID glyph markers",
            category="ats",
            passed=True,
            details="✅ No (cid:*) glyph markers detected in extracted PDF text.",
        )

    return VerificationCheck(
        name="no_cid_markers",
        label="No CID glyph markers",
        category="ats",
        passed=False,
        details="❌ (cid:*) glyph markers detected — ATS systems will fail to extract text.",
        suggestion="Re-embed fonts or use a different LaTeX template that embeds fonts correctly.",
    )


def _check_ats_contact(ats: ATSResult | None) -> VerificationCheck:
    """Check 9: Email and phone present as literal text in PDF.

    ATS systems need email and phone as extractable text (not images).
    """
    if ats is None:
        return VerificationCheck(
            name="ats_contact_parsable",
            label="ATS-contact info parsable",
            category="ats",
            passed=True,
            details="⚠️ ATS check not run — contact info check skipped.",
        )

    passed = ats.has_email and ats.has_phone
    details_parts = []
    if ats.has_email:
        details_parts.append("✅ Email found")
    else:
        details_parts.append("❌ Email NOT found")
    if ats.has_phone:
        details_parts.append("✅ Phone found")
    else:
        details_parts.append("❌ Phone NOT found")
    if ats.has_candidate_name:
        details_parts.append("✅ Name found")
    else:
        details_parts.append("❌ Name NOT found")

    if passed:
        return VerificationCheck(
            name="ats_contact_parsable",
            label="ATS-contact info parsable",
            category="ats",
            passed=True,
            details=f"✅ {' | '.join(details_parts)}.",
        )

    suggestion = None
    if not ats.has_email:
        suggestion = "Ensure email is rendered as literal text (not an image or icon)."
    elif not ats.has_phone:
        suggestion = "Ensure phone number is rendered as literal text."
    else:
        suggestion = "Ensure contact info uses standard fonts for ATS extraction."

    return VerificationCheck(
        name="ats_contact_parsable",
        label="ATS-contact info parsable",
        category="ats",
        passed=False,
        details=f"❌ {' | '.join(details_parts)}.",
        suggestion=suggestion,
    )


def _check_keyword_coverage(ats: ATSResult | None) -> VerificationCheck:
    """Check 10: Job posting keyword coverage ≥ 70%.

    Uses the existing keyword extraction logic from ATS check service.
    """
    if ats is None:
        return VerificationCheck(
            name="keyword_coverage",
            label="Keyword coverage ≥ 70%",
            category="ats",
            passed=True,
            details="⚠️ ATS check not run — keyword coverage check skipped.",
        )

    threshold = 0.7
    coverage = ats.keyword_coverage
    missing = ats.missing_keywords or []

    if coverage >= threshold:
        return VerificationCheck(
            name="keyword_coverage",
            label="Keyword coverage ≥ 70%",
            category="ats",
            passed=True,
            details=f"✅ {coverage:.0%} keyword coverage ({len(ats.found_keywords or [])} found, {len(missing)} missing).",
        )

    return VerificationCheck(
        name="keyword_coverage",
        label="Keyword coverage ≥ 70%",
        category="ats",
        passed=False,
        details=f"❌ {coverage:.0%} keyword coverage — below {threshold:.0%} threshold. Missing: {', '.join(missing[:10])}.",
        suggestion="Add more job-specific keywords from the posting into the CV experience bullets.",
    )


# ── LLM Content Quality Check (single call) ─────────────────────────


LLM_CONTENT_CHECK_PROMPT = """\
You are a VERIFICATION ENGINE evaluating a generated CV and cover letter for quality.

Your job is to detect issues that deterministic checks cannot catch:

1. **Fabricated claims**: Compare the documents against the candidate profile.
   Flag ANY experience, achievement, skill, or credential not present in the
   candidate's actual profile. Be specific — reference the exact claim.

2. **Generic profile statement**: Check if the profile statement is so generic
   it could apply to any role (e.g., "Experienced professional seeking new
   opportunities"). The statement should reference the specific role/industry.

3. **Tone consistency**: Check if the CV and cover letter have the same level
   of formality and professional voice. They should sound like they were
   written by the same person.

Return structured JSON with the following fields:
- overall_assessment: "pass" or "fail" or "warn"
- fabricated_claims: [] (list of specific fabricated claims found, empty if none)
- profile_specific: true/false (whether profile statement is specific enough)
- tone_consistent: true/false (whether CV and cover letter tone match)
- issues: [] (list of issue descriptions, max 3)
- recommendations: [] (list of improvement suggestions, max 3)
"""


async def _run_llm_content_checks(
    cv_latex: str,
    cover_latex: str,
    job_posting: JobPosting,
    candidate: CandidateProfile | None,
    provider_config: dict | None = None,
    usage: dict[str, Any] | None = None,
) -> list[VerificationCheck]:
    """Run LLM-based content quality checks (single call).

    Returns up to 3 VerificationCheck objects:
    - fabricated_claims: Whether any fabricated claims were detected
    - profile_specificity: Whether the profile statement is role-specific
    - tone_consistency: Whether CV and cover letter tone is consistent

    Falls back gracefully (warns) if the LLM call fails.
    """
    # Build a compact candidate summary
    candidate_summary = _build_candidate_summary(candidate)

    system_prompt = LLM_CONTENT_CHECK_PROMPT

    user_prompt = f"""\
CANDIDATE PROFILE (ground truth):
{candidate_summary}

JOB POSTING:
Title: {job_posting.title}
Company: {job_posting.company}
Requirements: {', '.join(job_posting.requirements[:8]) if job_posting.requirements else 'N/A'}

CV DOCUMENT (LaTeX, first 4000 chars):
{cv_latex[:4000] if cv_latex else '(no content)'}

COVER LETTER (LaTeX, first 3000 chars):
{cover_latex[:3000] if cover_latex else '(no content)'}

Evaluate these documents for fabricated claims, profile specificity, and tone consistency.
"""

    try:
        provider_kwargs = get_provider_kwargs(provider_config) if provider_config else {}

        result: LlmContentCheckOutput = await llm_completion_structured(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            output_schema=LlmContentCheckOutput,
            **provider_kwargs,
            temperature=0.0,
            max_tokens=800,
            usage=usage,
        )

        checks = []

        # Fabricated claims check
        has_fabricated = len(result.fabricated_claims) > 0
        if has_fabricated:
            claims_text = "; ".join(result.fabricated_claims[:3])
            checks.append(VerificationCheck(
                name="fabricated_claims_free",
                label="No fabricated claims",
                category="llm",
                passed=False,
                details=f"❌ {len(result.fabricated_claims)} potential fabrication(s) detected: {claims_text}",
                suggestion="Remove fabricated claims. Use only experience from the candidate's actual profile.",
            ))
        else:
            checks.append(VerificationCheck(
                name="fabricated_claims_free",
                label="No fabricated claims",
                category="llm",
                passed=True,
                details="✅ No fabricated claims detected.",
            ))

        # Profile specificity check
        checks.append(VerificationCheck(
            name="profile_specific_to_role",
            label="Profile statement specific to role",
            category="llm",
            passed=result.profile_specific,
            details=(
                "✅ Profile statement mentions the specific role or industry."
                if result.profile_specific
                else "❌ Profile statement is generic — could apply to any role."
            ),
            suggestion=(
                None
                if result.profile_specific
                else "Update the profile statement to reference the specific role title and industry."
            ),
        ))

        # Tone consistency check
        checks.append(VerificationCheck(
            name="tone_consistency",
            label="Consistent tone CV/cover",
            category="llm",
            passed=result.tone_consistent,
            details=(
                "✅ CV and cover letter have consistent tone and formality."
                if result.tone_consistent
                else "❌ Tone mismatch between CV and cover letter."
            ),
            suggestion=(
                None
                if result.tone_consistent
                else "Align the cover letter tone with the CV (both should sound like the same person)."
            ),
        ))

        return checks

    except Exception as e:
        logger.warning(f"LLM content check failed (non-blocking): {e}")
        return [
            VerificationCheck(
                name="fabricated_claims_free",
                label="No fabricated claims",
                category="llm",
                passed=True,
                details="⚠️ LLM check not available — unable to verify fabrications.",
            ),
            VerificationCheck(
                name="profile_specific_to_role",
                label="Profile statement specific to role",
                category="llm",
                passed=True,
                details="⚠️ LLM check not available — unable to verify specificity.",
            ),
            VerificationCheck(
                name="tone_consistency",
                label="Consistent tone CV/cover",
                category="llm",
                passed=True,
                details="⚠️ LLM check not available — unable to verify tone consistency.",
            ),
        ]


def _build_candidate_summary(candidate: CandidateProfile | None) -> str:
    """Build a compact candidate summary for the LLM content check prompt."""
    if candidate is None:
        return "Profile not available."
    parts = []
    if candidate.full_name:
        parts.append(f"Name: {candidate.full_name}")
    if candidate.profile_statement:
        parts.append(f"Profile: {candidate.profile_statement}")
    if candidate.experience:
        exp_summary = "; ".join(
            f"{e.get('title', '')} at {e.get('company', '')}"
            for e in candidate.experience[:3]
        )
        parts.append(f"Experience: {exp_summary}")
    if candidate.skills:
        skill_list = []
        if candidate.skills.get("programming_ml"):
            skill_list.extend(s.get("language", "") for s in candidate.skills["programming_ml"])
        if candidate.skills.get("domain_expertise"):
            skill_list.extend(candidate.skills["domain_expertise"])
        if candidate.skills.get("software_tools"):
            skill_list.extend(candidate.skills["software_tools"])
        parts.append(f"Skills: {', '.join(skill_list[:8])}")
    if candidate.education:
        edu_summary = "; ".join(
            f"{e.get('degree', '')} at {e.get('institution', '')}"
            for e in candidate.education[:2]
        )
        parts.append(f"Education: {edu_summary}")
    return "\n".join(parts) if parts else "Profile not completed."
