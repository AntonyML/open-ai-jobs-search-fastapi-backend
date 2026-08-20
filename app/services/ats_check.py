"""ATS parseability check service — verifies CVs are ATS-compatible.

This service checks whether a CV (from structured JSON or PDF) would pass
through Applicant Tracking System (ATS) parsers without losing critical
information.

All checks are 100% DETERMINISTIC — no LLM calls. This is intentional:
the ATS check is a quality gate, not an AI analysis.

Primary entry point: ``check_ats_from_json()`` — works directly on
structured CV data without needing PDF files or external binaries.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from app.schemas.ats_check import ATSResult

if TYPE_CHECKING:
    from app.db.models import CandidateProfile, JobPosting

from app.core.logging import get_logger, bind_context
logger = get_logger(__name__)

# ── Regular expressions ─────────────────────────────────────────────

# Basic email pattern for ATS-extracted text (not for validation, just detection)
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Phone patterns covering international formats
_PHONE_PATTERN = re.compile(
    r"(\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}"
)

# CID marker pattern — indicates fonts not embedded correctly
_CID_PATTERN = re.compile(r"\(cid:\d+\)")

# Minimum keyword coverage threshold (fraction of required keywords found)
_KEYWORD_COVERAGE_THRESHOLD = 0.7





# ── Check functions ─────────────────────────────────────────────────


def _check_cid_markers(text: str) -> bool:
    """Check if the extracted text contains (cid:*) glyph markers.

    CID markers indicate that the PDF uses fonts where characters are
    not mapped to Unicode. ATS parsers cannot read such text, making
    the PDF effectively invisible to automated systems.

    Returns:
        True if CID markers are found (BAD for ATS).
    """
    return bool(_CID_PATTERN.search(text))


def _check_email(text: str, candidate_email: str | None) -> bool:
    """Check if the candidate's email appears as literal text in the PDF.

    If the email is not extractable as text (e.g., rendered as an image
    or icon), ATS systems cannot parse it.

    Args:
        text: Extracted PDF text.
        candidate_email: The candidate's email from their profile.

    Returns:
        True if the email is found in the extracted text.
    """
    if not candidate_email:
        return bool(_EMAIL_PATTERN.search(text))
    return candidate_email.lower() in text.lower()


def _check_phone(text: str, candidate_phone: str | None) -> bool:
    """Check if a phone number appears as literal text in the PDF.

    Args:
        text: Extracted PDF text.
        candidate_phone: The candidate's phone from their profile.

    Returns:
        True if a phone number is found in the extracted text.
    """
    if candidate_phone:
        # Normalise both strings for comparison
        normalised_phone = re.sub(r"[\s\-\.\(\)]", "", candidate_phone)
        normalised_text = re.sub(r"[\s\-\.\(\)]", "", text)
        return normalised_phone.lower() in normalised_text.lower()
    return bool(_PHONE_PATTERN.search(text))


def _check_candidate_name(text: str, candidate_name: str | None) -> bool:
    """Check if the candidate's name appears as literal text.

    Args:
        text: Extracted PDF text.
        candidate_name: The candidate's full name from their profile.

    Returns:
        True if the name is found in the extracted text.
    """
    if not candidate_name:
        return False
    return candidate_name.lower() in text.lower()


def _check_keywords(
    text: str, job_posting: JobPosting
) -> tuple[float, list[str], list[str]]:
    """Check keyword coverage against the job posting requirements.

    Extracts keywords from the job posting (description + requirements)
    and checks which ones appear in the PDF's extracted text.

    Keywords are normalised: lowercased, stemmed (simple prefix matching
    for common suffixes like -ing, -ed, -s).

    Returns:
        Tuple of (coverage_ratio, found_keywords, missing_keywords).
    """
    # Build keyword set from job posting
    keywords: set[str] = set()

    # Common stop words to exclude from ALL keyword sources
    stop_words = {
        "the", "and", "for", "with", "this", "that", "from", "have",
        "will", "your", "what", "about", "which", "their", "would",
        "could", "should", "been", "were", "also", "than", "into",
        "over", "such", "only", "other", "more", "very", "just",
    }

    if job_posting.requirements:
        for req in job_posting.requirements:
            # Extract meaningful words (3+ chars, exclude stop words)
            words = re.findall(r"\b[a-zA-Z]{3,}\b", req.lower())
            keywords.update(w for w in words if w not in stop_words)

    if job_posting.description:
        # Extract key terms: capitalized phrases, technical terms
        desc_lower = job_posting.description.lower()
        tech_terms = re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b", job_posting.description)
        keywords.update(t.lower() for t in tech_terms)

        # Add single-word technical terms (3+ chars), exclude stop words
        words = re.findall(r"\b[a-zA-Z]{3,}\b", desc_lower)
        keywords.update(w for w in words if w not in stop_words)

    if not keywords:
        return 1.0, [], []

    # Normalise text for matching
    text_lower = text.lower()

    # Check each keyword with simple stemming
    found: list[str] = []
    missing: list[str] = []

    for keyword in sorted(keywords):
        # Simple stem matching: check if the keyword (or its stem) appears
        # ATS-style matching: the keyword must appear as a whole word
        pattern = re.compile(r"\b" + re.escape(keyword) + r"\w*\b")
        if pattern.search(text_lower):
            found.append(keyword)
        else:
            missing.append(keyword)

    coverage = len(found) / len(keywords) if keywords else 1.0
    return coverage, found, missing


def _detect_column_scramble(text: str) -> bool:
    """Detect if text reading order is scrambled by multi-column layout.

    Heuristic: if short lines (< 30 chars) regularly alternate with long
    lines (> 80 chars) in a pattern, the PDF likely uses a multi-column
    layout and pdftotext -layout didn't reconstruct order correctly.

    This is a best-effort check — it will have false positives for
    genuine multi-column content, but it's better than silently serving
    unreadable PDFs to ATS systems.

    Returns:
        True if column scrambling is suspected.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) < 20:
        return False

    # Sample every 5th line
    short_count = 0
    long_count = 0
    transitions = 0
    prev_short = False

    for i in range(0, len(lines), 5):
        line = lines[i]
        is_short = len(line) < 30
        is_long = len(line) > 80

        if is_short:
            short_count += 1
        if is_long:
            long_count += 1

        if i > 0 and is_short != prev_short:
            # Only count transitions between meaningful states
            if is_short or prev_short:
                transitions += 1

        prev_short = is_short

    # If we see frequent short↔long transitions, suspect column scramble
    sample_size = max(len(lines) // 5, 1)
    if transitions > sample_size * 0.4 and short_count > 0 and long_count > 0:
        return True

    return False


# ── Legacy PDF-based check (DEPRECATED — use check_ats_from_json) ──


async def check_ats_parseability(
    pdf_path: Path,  # noqa: ARG001
    job_posting: JobPosting,
    candidate: CandidateProfile | None = None,
) -> ATSResult:
    """DEPRECATED: Use check_ats_from_json() instead.

    This function relied on pdftotext (poppler) which is no longer
    a dependency. Kept for backward compatibility — always returns
    a soft-fail result.
    """
    import warnings

    warnings.warn(
        "check_ats_parseability is deprecated, use check_ats_from_json",
        DeprecationWarning,
        stacklevel=2,
    )
    logger.warning("check_ats_parseability called (deprecated) — returning soft fail")
    return ATSResult(
        raw_text=None,
        has_cid_markers=False,
        has_email=False,
        has_phone=False,
        has_candidate_name=False,
        keyword_coverage=0.0,
        found_keywords=[],
        missing_keywords=[],
        reading_order_ok=True,
        pass_ats=False,
    )


# ── Structured data ATS check (no PDF needed) ──────────────────────


def extract_text_from_cv_json(cv_json: dict) -> str:
    """Extract all renderable text from a structured CV JSON.

    This text is equivalent to what an ATS would read from the PDF.
    Used for keyword matching, email/phone/name verification, etc.
    """
    cv = cv_json.get("cv", cv_json)
    parts: list[str] = []

    # Header fields
    for field in [
        "first_name", "last_name", "email", "phone",
        "location", "linkedin", "github", "portfolio_url",
    ]:
        if cv.get(field):
            parts.append(str(cv[field]))

    # Profile statement
    if cv.get("profile_statement"):
        parts.append(cv["profile_statement"])

    # Core competencies
    parts.extend(cv.get("core_competencies") or [])

    # Skills
    for group in (cv.get("skills") or []):
        if isinstance(group, dict):
            parts.append(group.get("label", ""))
            parts.extend(group.get("skills", []))
        elif isinstance(group, str):
            parts.append(group)

    # Experience
    for exp in (cv.get("experience") or []):
        parts.append(exp.get("title", ""))
        parts.append(exp.get("company", ""))
        parts.append(exp.get("location", ""))
        parts.extend(exp.get("bullets") or [])

    # Projects
    for proj in (cv.get("projects") or []):
        parts.append(proj.get("name", ""))
        parts.append(proj.get("description", ""))

    # Education
    for edu in (cv.get("education") or []):
        parts.append(edu.get("degree", ""))
        parts.append(edu.get("institution", ""))
        parts.append(edu.get("key_topics", ""))

    # Certifications
    for cert in (cv.get("certifications") or []):
        parts.append(cert.get("name", ""))
        parts.append(cert.get("issuer", ""))

    # Publications
    for pub in (cv.get("publications") or []):
        parts.append(pub.get("title", ""))
        parts.append(pub.get("journal", ""))

    # Awards
    for award in (cv.get("awards") or []):
        parts.append(award.get("award", ""))
        parts.append(award.get("event", ""))

    # References
    for ref in (cv.get("references") or []):
        parts.append(ref.get("name", ""))
        parts.append(ref.get("title", ""))
        parts.append(ref.get("company", ""))

    # Cover letter
    cl = cv.get("cover_letter")
    if cl and isinstance(cl, dict):
        parts.append(cl.get("opening_paragraph", ""))
        parts.extend(cl.get("body_paragraphs") or [])
        parts.append(cl.get("company_connection_paragraph", ""))
        parts.append(cl.get("closing_paragraph", ""))

    return " ".join(p for p in parts if p)


async def check_ats_from_json(
    cv_json: dict,
    job_posting: JobPosting,
    candidate: CandidateProfile | None = None,
) -> ATSResult:
    """ATS parseability check directly on structured data.

    Replaces check_ats_parseability() for the main pipeline.
    No PDF files or pdftotext needed.
    """
    with bind_context(stage="ats_check_json"):
        logger.info("Starting ATS check (JSON mode)")

        # Extract text from structured data
        raw_text = extract_text_from_cv_json(cv_json)

        if not raw_text.strip():
            return ATSResult(
                raw_text=None,
                has_cid_markers=False,
                has_email=False,
                has_phone=False,
                has_candidate_name=False,
                keyword_coverage=0.0,
                found_keywords=[],
                missing_keywords=[],
                reading_order_ok=True,
                pass_ats=False,
            )

        # CID markers: not applicable in JSON mode
        has_cid = False

        # Contact info: direct field lookup
        candidate_email = candidate.email if candidate else None
        candidate_phone = candidate.phone if candidate else None
        candidate_name = candidate.full_name if candidate else None

        has_email = _check_email(raw_text, candidate_email)
        has_phone = _check_phone(raw_text, candidate_phone)
        has_name = _check_candidate_name(raw_text, candidate_name)

        # Keyword coverage
        coverage, found_keywords, missing_keywords = _check_keywords(raw_text, job_posting)

        # Reading order: not applicable in JSON mode
        reading_order_ok = True

        # Overall verdict
        critical_checks = [
            not has_cid,
            coverage >= _KEYWORD_COVERAGE_THRESHOLD,
            has_email,
            has_name,
        ]
        pass_ats = all(critical_checks)

        return ATSResult(
            raw_text=raw_text[:500],
            has_cid_markers=has_cid,
            has_email=has_email,
            has_phone=has_phone,
            has_candidate_name=has_name,
            keyword_coverage=round(coverage, 2),
            found_keywords=found_keywords[:30],
            missing_keywords=missing_keywords[:30],
            reading_order_ok=reading_order_ok,
            pass_ats=pass_ats,
        )
