"""PDF Verifier — standalone utility for verifying generated PDFs.

Adapted from MadsLorentzen/ai-job-search tools/verify_pdf.py.

Checks whether a compiled PDF is ATS-parseable:
1. Text is extractable (via pdftotext)
2. No (cid:*) glyph markers (fonts not mapped to Unicode)
3. Candidate name and email appear as literal text
4. Keyword coverage meets threshold (≥70%)
5. Reading order is not scrambled by multi-column layout

This is a convenience wrapper around the existing ats_check service,
usable both as a standalone function and as an importable utility.

100% DETERMINISTIC — no LLM calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.schemas.ats_check import ATSResult
from app.services.ats_check import check_ats_parseability as _ats_check

if TYPE_CHECKING:
    from app.db.models import CandidateProfile, JobPosting


async def verify_pdf(
    pdf_path: str | Path,
    job_posting: JobPosting,
    candidate: CandidateProfile | None = None,
) -> ATSResult:
    """Verify that a PDF is ATS-compatible.

    Args:
        pdf_path: Path to the compiled PDF file.
        job_posting: JobPosting ORM object (for keyword extraction).
        candidate: Optional CandidateProfile (for email/phone/name checks).

    Returns:
        ATSResult with all check outcomes and overall verdict.

    Example:
        ```python
        result = await verify_pdf(
            pdf_path="/tmp/cv.pdf",
            job_posting=job,
            candidate=profile,
        )
        if result.pass_ats:
            print("PDF is ATS-compatible!")
        else:
            print(f"Missing keywords: {result.missing_keywords}")
        ```
    """
    pdf = Path(pdf_path) if isinstance(pdf_path, str) else pdf_path
    return await _ats_check(pdf, job_posting, candidate)


def verify_pdf_sync(
    pdf_path: str | Path,
    job_posting: "JobPosting",
    candidate: "CandidateProfile | None" = None,
) -> ATSResult:
    """Synchronous wrapper for verify_pdf.

    NOTE: Uses ``asyncio.run()`` internally, which will fail if called
    from within a running async event loop (e.g., inside a FastAPI
    endpoint or async test). This wrapper is intended for:
    - Scripts and CLI tools
    - Synchronous test helpers
    - Background threads

    For async contexts, use ``verify_pdf()`` directly.

    Args:
        pdf_path: Path to the compiled PDF file.
        job_posting: JobPosting ORM object.
        candidate: Optional CandidateProfile.

    Returns:
        ATSResult with all check outcomes.
    """
    import asyncio
    return asyncio.run(verify_pdf(pdf_path, job_posting, candidate))
