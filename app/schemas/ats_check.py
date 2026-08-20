"""Pydantic schemas for the ATS parseability check.

ATS (Applicant Tracking System) compatibility verification happens after
PDF compilation. It checks whether the generated PDF would pass through
automated parsing systems without losing critical information.

100% deterministic — no LLM calls.
"""

from pydantic import BaseModel, Field


class ATSResult(BaseModel):
    """Result of an ATS parseability check on a compiled PDF.

    All checks are deterministic (no LLM calls). The ATS check runs
    after PDF compilation and before the application is marked complete.

    Attributes:
        raw_text: Full text extracted from PDF via pdftotext -layout.
        has_cid_markers: True if (cid:*) glyph markers found (fonts not
            embedded correctly → ATS cannot read text).
        has_email: True if email found as literal text in extracted output.
        has_phone: True if phone number found as literal text.
        has_candidate_name: True if candidate name found in extracted text.
        keyword_coverage: Fraction (0.0-1.0) of job posting keywords found
            in the extracted PDF text.
        found_keywords: List of job keywords that were found.
        missing_keywords: List of job keywords that were NOT found.
        reading_order_ok: True if text appears in natural reading order
            (not scrambled by multi-column layout).
        pass_ats: Overall ATS compatibility verdict. True only when ALL
            critical checks pass: no CID markers, keyword_coverage >= 0.7,
            email and name present as extractable text.
    """

    raw_text: str | None = Field(None, description="Full extracted text from PDF")
    has_cid_markers: bool = Field(False, description="(cid:*) glyph markers detected")
    has_email: bool = Field(False, description="Email found as extractable text")
    has_phone: bool = Field(False, description="Phone found as extractable text")
    has_candidate_name: bool = Field(False, description="Candidate name found in PDF text")
    keyword_coverage: float = Field(
        0.0, ge=0.0, le=1.0, description="Fraction of job keywords found in PDF"
    )
    found_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    reading_order_ok: bool = Field(True, description="Text reading order appears correct")
    pass_ats: bool = Field(False, description="Overall ATS compatibility verdict")
