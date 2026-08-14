"""JSON CV schema — structured representation of a tailored CV and cover letter.

The schema is profession-agnostic. Skill groups are free-label categories so
the same model serves a developer, an electrical engineer, a UX designer,
a data analyst, or a DevOps engineer equally well.

The rendered CV (``CV``) is separated from generation metadata (``CVMetadata``)
so the Typst template never sees pipeline internals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# ── Shared value objects ──────────────────────────────────────────────


ProficiencyLevel = Literal["beginner", "intermediate", "advanced", "expert"]
LanguageProficiency = Literal["native", "fluent", "advanced", "intermediate", "basic"]


class DateRange(BaseModel):
    """Flexible date representation for roles, education, projects.

    Both fields are optional so a single ``start`` with no ``end`` (current role,
    one-off project) and completely open-ended entries (``null``, ``null`` for
    gaps) are valid.  ``end="Present"`` is the expected convention for ongoing.
    """

    start: str | None = Field(None, description="Start date (YYYY-MM or YYYY).")
    end: str | None = Field(None, description="End date (YYYY-MM or YYYY), or 'Present'.")


class Skill(BaseModel):
    """A single skill with optional proficiency and related tools/frameworks."""

    name: str = Field(..., description="Skill name (e.g. 'Python', 'Figma', 'PMP').")
    proficiency: ProficiencyLevel | None = Field(None, description="Self-assessed level.")
    frameworks: list[str] | None = Field(None, description="Related frameworks, libraries, or tools.")


class SkillGroup(BaseModel):
    """A labelled group of skills — the core flexibility mechanism.

    The LLM chooses the label based on profession:
    - Developer:  ``{"label": "Languages", "skills": [...]}``
    - UX:         ``{"label": "Design Tools", "skills": [...]}``
    - EE:         ``{"label": "CAD / Simulation", "skills": [...]}``
    - DevOps:     ``{"label": "Infrastructure", "skills": [...]}``

    Human languages also use this model (label = "Languages").
    """

    label: str = Field(..., description="Category label chosen by the LLM (e.g. 'Languages', 'Design Tools').")
    skills: list[Skill] = Field(..., description="Skills in this group.")


# ── CV sections ───────────────────────────────────────────────────────


class ExperienceEntry(BaseModel):
    """A single tailored work experience entry."""

    title: str = Field(..., description="Job title (tailored to target role).")
    company: str = Field(..., description="Company or organization name.")
    location: str | None = Field(None, description="Work location (city, region, or remote).")
    date_range: DateRange = Field(default_factory=DateRange)
    bullets: list[str] = Field(
        default_factory=list,
        description="X-Y-Z formatted bullet points.",
        max_length=8,
    )


class EducationEntry(BaseModel):
    """Education entry with structured dates and optional detail."""

    degree: str = Field(..., description="Degree name (e.g. 'M.Sc. in Computer Science').")
    institution: str = Field(..., description="University or institution.")
    date_range: DateRange = Field(default_factory=DateRange)
    period: str | None = Field(None, description="Display-friendly alternative to date_range (e.g. '2016–2020').")
    key_topics: list[str] | None = Field(None, description="Relevant coursework, thesis, or specialisation.")


class ProjectEntry(BaseModel):
    """A project or portfolio piece — more flexible than experience bullets."""

    name: str = Field(..., description="Project name.")
    url: str | None = Field(None, description="Live URL or repository.")
    description: str | None = Field(None, description="1–2 sentence summary.")
    technologies: list[str] | None = Field(None, description="Key technologies or methods used.")


class CertificationEntry(BaseModel):
    """Professional certification or license."""

    name: str = Field(..., description="Certification name (e.g. 'AWS Solutions Architect').")
    issuer: str = Field(..., description="Issuing body (e.g. 'Amazon', 'PMP').")
    year: str | None = Field(None, description="Year obtained.")
    url: str | None = Field(None, description="Verification URL.")


class PublicationEntry(BaseModel):
    """Academic or industry publication."""

    authors: str = Field(..., description="Author list (e.g. 'J. Doe, J. Smith').")
    year: str = Field(..., description="Publication year.")
    title: str = Field(..., description="Publication title.")
    journal: str | None = Field(None, description="Journal, conference, or venue.")
    doi: str | None = Field(None, description="DOI if available.")


class AwardEntry(BaseModel):
    """Honor, award, or recognition."""

    name: str = Field(..., description="Award name.")
    issuer: str | None = Field(None, description="Issuing organisation or event.")
    year: str | None = Field(None, description="Year received.")


class ReferenceEntry(BaseModel):
    """Professional reference."""

    name: str = Field(..., description="Reference name.")
    title: str | None = Field(None, description="Job title.")
    company: str | None = Field(None, description="Company or organisation.")
    email: str | None = Field(None, description="Email address.")
    phone: str | None = Field(None, description="Phone number.")


# ── Cover letter ──────────────────────────────────────────────────────


class CoverLetter(BaseModel):
    """Structured cover letter."""

    opening_paragraph: str = Field(..., description="Salutation + hook.")
    body_paragraphs: list[str] = Field(
        ...,
        description="2–4 body paragraphs highlighting fit, skills, or achievements.",
        max_length=4,
    )
    company_connection_paragraph: str | None = Field(
        None,
        description="Connection to the company's mission, product, or recent work.",
    )
    closing_paragraph: str = Field(..., description="Call to action + thank you.")


# ── Full CV document (renderable only) ────────────────────────────────


class CV(BaseModel):
    """Complete structured CV — what the Typst template renders.

    Every section beyond the header is optional so the same schema handles
    profiles with different section combinations.  The Typst template iterates
    over ``sections`` or accesses named fields directly — no metadata leaks in.
    """

    # ── Header ─────────────────────────────────────────────────────
    first_name: str = Field(..., description="Candidate first name.")
    last_name: str = Field(..., description="Candidate last name.")
    email: EmailStr = Field(..., description="Email address.")
    phone: str | None = Field(None, description="Phone number (international format).")
    location: str | None = Field(None, description="City and country (e.g. 'Copenhagen, Denmark').")
    linkedin: str | None = Field(None, description="Full LinkedIn URL.")
    github: str | None = Field(None, description="Full GitHub URL.")
    portfolio_url: str | None = Field(None, description="Personal website, portfolio, or blog URL.")
    language: str | None = Field(
        None,
        description="ISO 639-1 code of the CV language (e.g. 'en', 'da', 'es'). "
        "Affects hyphenation and font selection at render time.",
    )

    # ── Profile ────────────────────────────────────────────────────
    profile_statement: str | None = Field(
        None,
        description="2–3 sentence professional summary. Mentions target role title.",
    )

    # ── Core competencies ──────────────────────────────────────────
    core_competencies: list[str] | None = Field(
        None,
        description="8–15 high-level headline competencies for the tag-cloud section "
        "at the top of the CV (e.g. 'System Design', 'UX Research', 'Embedded Systems'). "
        "Distinct from ``skills``: competencies are headlines, skills carry proficiency levels.",
        max_length=15,
    )

    # ── Skills (detailed breakdown) ────────────────────────────────
    skills: list[SkillGroup] | None = Field(
        None,
        description="Detailed skills grouped by free-label categories. "
        "The LLM chooses labels per profession. "
        "Human languages are one group (label = 'Languages').",
    )

    # ── Experience ─────────────────────────────────────────────────
    experience: list[ExperienceEntry] = Field(
        ...,
        description="Tailored professional experience entries.",
        max_length=6,
    )

    # ── Projects / Portfolio ───────────────────────────────────────
    projects: list[ProjectEntry] | None = Field(
        None,
        description="Key projects or portfolio pieces (especially relevant for developers "
        "and designers).",
        max_length=6,
    )

    # ── Education ──────────────────────────────────────────────────
    education: list[EducationEntry] | None = Field(
        None,
        description="Education background (max ~3 entries).",
        max_length=3,
    )

    # ── Certifications ─────────────────────────────────────────────
    certifications: list[CertificationEntry] | None = Field(
        None,
        description="Professional certifications and licenses (e.g. AWS, PMP, PE licence).",
        max_length=5,
    )

    # ── Publications ───────────────────────────────────────────────
    publications: list[PublicationEntry] | None = Field(
        None,
        description="Academic or industry publications.",
        max_length=10,
    )

    # ── Awards ─────────────────────────────────────────────────────
    awards: list[AwardEntry] | None = Field(
        None,
        description="Honors, awards, and recognitions.",
        max_length=5,
    )

    # ── References ─────────────────────────────────────────────────
    references: list[ReferenceEntry] | None = Field(
        None,
        description="Professional references.",
        max_length=3,
    )

    # ── Cover letter ───────────────────────────────────────────────
    cover_letter: CoverLetter | None = Field(None, description="Tailored cover letter.")


# ── Generation metadata (separate from renderable CV) ─────────────────


class IncorporatedKeyword(BaseModel):
    """Tracks how a job-posting keyword was incorporated into the CV."""

    keyword: str = Field(..., description="The job-posting keyword.")
    where_incorporated: str = Field(
        ...,
        description="Section and context (e.g. 'experience bullet 2', 'skills group Frameworks').",
    )
    original_context: str | None = Field(
        None,
        description="How the candidate's profile supported this keyword (defensibility).",
    )


class AddressedRedFlag(BaseModel):
    """Tracks how a ranking red flag was addressed in the CV."""

    red_flag: str = Field(..., description="Red flag identified during ranking.")
    how_addressed: str = Field(..., description="How the CV addresses it (reframing, emphasis, etc.).")


class CVMetadata(BaseModel):
    """Generation metadata — NOT rendered into the PDF.

    Lives alongside ``CV`` in ``GenerateCVOutput`` for the pipeline stages
    (reviewer, verification checklist) but never reaches the Typst template.
    """

    language: str | None = Field(
        None,
        description="ISO 639-1 code of the generated content (e.g. 'en', 'da', 'es'). "
        "Shared with ``CV.language`` for convenience.",
    )
    incorporated_keywords: list[IncorporatedKeyword] | None = Field(
        None,
        description="Keywords from the job posting incorporated into the CV.",
    )
    addressed_red_flags: list[AddressedRedFlag] | None = Field(
        None,
        description="Red flags from ranking that were addressed.",
    )


# ── Drafter output wrapper ────────────────────────────────────────────


class GenerateCVOutput(BaseModel):
    """Top-level output from the drafter LLM call.

    Separates the renderable CV from pipeline metadata so the Typst template
    is never exposed to generation internals.
    """

    cv: CV = Field(..., description="The renderable CV (consumed by Typst templates).")
    metadata: CVMetadata = Field(
        default_factory=CVMetadata,
        description="Generation metadata (used by reviewer, verification checklist).",
    )


# ── CV generator API (POST /cv/base, POST /cv/personalize) ────────────


class CVAnalysis(BaseModel):
    """Recruiter-lens analysis of a job description against the candidate profile.

    Produced before drafting so the drafter can inject missing keywords and
    preemptively address red flags.
    """

    match_score: int = Field(
        ...,
        description="Estimated match score 0–100 (keyword/skill overlap with the job).",
        ge=0,
        le=100,
    )
    missing_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords in the job description the candidate should emphasize.",
    )
    red_flags: list[str] = Field(
        default_factory=list,
        description="Potential concerns a recruiter could raise about this candidate.",
    )
    adapted_experience: list[str] = Field(
        default_factory=list,
        description="Concrete reframing suggestions the drafter should apply.",
    )


class CVBaseCreate(BaseModel):
    """Request for POST /cv/base — generate a generic base CV (no job context)."""


class CVPersonalizeCreate(BaseModel):
    """Request for POST /cv/personalize — tailor the base CV to a job description.

    Only the description text is required; it is passed directly to the LLM
    pipeline (no scraping, no JobPosting record needed).
    """

    job_description_text: str = Field(
        ...,
        description="Full job posting text to tailor the CV against.",
        min_length=50,
        max_length=20000,
    )


class CVRecoverCreate(BaseModel):
    """Request for POST /cv/base/recover — restore a previous (obsolete) base CV.

    The swap never creates a third document: the restored CV becomes active
    and the current active base CV is demoted to obsolete.
    """

    cv_id: str = Field(..., description="ID of the obsolete base CV to restore.")


class CVPersonalizeJobCreate(BaseModel):
    """Request for POST /cv/personalize-job — adapt the base CV to an existing job posting.

    The adapted CV is generated from the user's base CV + the full context of
    the selected offer (title, company, description, requirements, ...). The
    base CV record is never modified.
    """

    base_cv_id: str = Field(..., description="ID of the user's base CV.")
    job_posting_id: str = Field(..., description="ID of an existing job posting (offer).")


class CVJobOut(BaseModel):
    """Lightweight reference to the job posting an adapted CV was generated from."""

    id: str = Field(..., description="Job posting ID.")
    title: str = Field(..., description="Job title.")
    company: str | None = Field(None, description="Company name.")
    location: str | None = Field(None, description="Job location.")


class CVResponse(BaseModel):
    """API response for a generated CV."""

    cv_id: str = Field(..., description="ID of the generated CV record.")
    cv_type: Literal["base", "personalized"] = Field(
        ..., description="'base' for generic, 'personalized' for job-tailored."
    )
    base_status: Literal["active", "obsolete"] | None = Field(
        None,
        description="'active' for the current base CV, 'obsolete' for a replaced base CV "
        "still listed in Mis CV; None for personalized CVs.",
    )
    job_url: str | None = Field(None, description="Source URL, when a job posting was used.")
    job_posting_id: str | None = Field(
        None, description="ID of the job posting this CV was adapted from (personalized CVs)."
    )
    job: CVJobOut | None = Field(
        None, description="Job posting reference for adapted CVs (title, company, location)."
    )
    job_description_text: str | None = Field(None, description="Job text used for personalization.")
    json_cv: dict[str, Any] = Field(..., description="The structured CV (CV + metadata).")
    pdf_url: str | None = Field(None, description="Download URL for the compiled PDF.")
    analysis: CVAnalysis | None = Field(None, description="Recruiter-lens analysis, when available.")
    created_at: datetime = Field(..., description="Creation timestamp.")
