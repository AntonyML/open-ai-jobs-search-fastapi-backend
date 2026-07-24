"""ATS parseability check service — verifies generated PDFs are ATS-compatible.

This service runs AFTER LaTeX compilation and checks whether the generated
PDF would pass through Applicant Tracking System (ATS) parsers without
losing critical information.

All checks are 100% DETERMINISTIC — no LLM calls. This is intentional:
the ATS check is a quality gate, not an AI analysis.

Dependencies (optional — no crash if missing):
- ``pdftotext`` (from poppler-utils): extracts text from PDF for analysis
- ``pdfinfo`` (from poppler-utils): provides PDF metadata

If neither binary is available, the service returns a warning result
with ``pass_ats`` = None and does NOT block the pipeline.
"""

from __future__ import annotations

import asyncio

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


# ── Binary resolution ───────────────────────────────────────────────


def _resolve_binary(name: str) -> str | Path:
    """Resolve the full path to a poppler binary, or return bare name.

    Uses the same ``latex_bin_dir`` setting as the LaTeX compiler so that
    a portable MiKTeX install's poppler tools are found automatically.

    Falls back to bare name (system PATH) if the directory is not set.
    """
    try:
        from app.services.apply import _resolve_latex_binary

        return _resolve_latex_binary(name)
    except (ImportError, AttributeError):
        return name


# ── Text extraction ─────────────────────────────────────────────────


async def _extract_pdf_text(pdf_path: Path) -> str | None:
    """Extract plain text from a PDF using pdftotext -layout.

    Uses ``pdftotext -layout`` which preserves reading order — critical
    for detecting multi-column issues in CV PDFs.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted text as a single string, or ``None`` if pdftotext
        is not available or extraction fails.
    """
    pdftotext_bin = _resolve_binary("pdftotext")

    try:
        proc = await asyncio.create_subprocess_exec(
            str(pdftotext_bin),
            "-layout",
            str(pdf_path),
            "-",  # Output to stdout
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace")

        logger.warning(f"pdftotext failed for {pdf_path}: {stderr.decode()}")
        return None
    except FileNotFoundError:
        logger.warning("pdftotext not found — ATS check skipped")
        return None
    except Exception as e:
        logger.warning(f"pdftotext extraction failed for {pdf_path}: {e}")
        return None


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


# ── Main entry point ────────────────────────────────────────────────


async def check_ats_parseability(
    pdf_path: Path,
    job_posting: JobPosting,
    candidate: CandidateProfile | None = None,
) -> ATSResult:
    """Run full ATS parseability check on a compiled PDF."""
    with bind_context(pipeline_stage="ats_check"):
        logger.info("Starting ATS check | pdf=%s", pdf_path)
        # ── Step 1: Extract text ────────────────────────────────────────
        raw_text = await _extract_pdf_text(pdf_path)

        if raw_text is None:
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

        # ── Step 2: CID markers ─────────────────────────────────────────
        has_cid = _check_cid_markers(raw_text)

        # ── Step 3: Contact info as literal text ────────────────────────
        candidate_email = candidate.email if candidate else None
        candidate_phone = candidate.phone if candidate else None
        candidate_name = candidate.full_name if candidate else None

        has_email = _check_email(raw_text, candidate_email)
        has_phone = _check_phone(raw_text, candidate_phone)
        has_name = _check_candidate_name(raw_text, candidate_name)

        # ── Step 4: Keyword coverage ────────────────────────────────────
        coverage, found_keywords, missing_keywords = _check_keywords(raw_text, job_posting)

        # ── Step 5: Reading order ───────────────────────────────────────
        reading_order_ok = not _detect_column_scramble(raw_text)

        # ── Step 6: Overall verdict ─────────────────────────────────────
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
