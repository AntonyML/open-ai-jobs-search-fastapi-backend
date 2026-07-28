"""JSON CV schema — structured representation of a tailored CV and cover letter.

This schema is the single source of truth for the Typst rendering pipeline.
The LLM generates JSON matching this schema (instead of LaTeX), and the
Typst templates consume it directly.

The schema must handle real-world variability:
- Variable-length bullets
- Employment gaps (no start/end dates, or present-tense "Present" entries)
- Multiple concurrent experiences
- Skills with proficiency levels and optional frameworks
- Optional sections (publications, awards, languages, references)
- Cover letter as a separate but co-designed structure
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Shared value objects ──────────────────────────────────────────────


class DateRange(BaseModel):
    """Flexible date representation — handles gaps and open-ended roles."""

    start: str | None = Field(None, description="Start date (YYYY-MM or YYYY). Null if unknown.")
    end: str | None = Field(None, description="End date (YYYY-MM or YYYY), or 'Present'. Null if unknown.")


class Skill(BaseModel):
    """A single skill with optional proficiency and related frameworks."""

    name: str = Field(..., description="Skill name (e.g. 'Python', 'TensorFlow', 'Agile').")
    proficiency: str | None = Field(None, description="Proficiency level: 'beginner', 'intermediate', 'advanced', 'expert'.")
    frameworks: list[str] | None = Field(None, description="Related frameworks / libraries.")


# ── CV sections ───────────────────────────────────────────────────────


class ExperienceEntry(BaseModel):
    """A single tailored work experience entry."""

    title: str = Field(..., description="Job title (tailored to target role).")
    company: str = Field(..., description="Company or organization name.")
    location: str | None = Field(None, description="Work location (city, state/country, remote).")
    date_range: DateRange = Field(default_factory=DateRange)
    bullets: list[str] = Field(
        default_factory=list,
        description="X-Y-Z formatted bullet points (max ~6 per entry).",
    )


class EducationEntry(BaseModel):
    """Education entry, typically with optional details."""

    degree: str = Field(..., description="Degree name (e.g. 'M.Sc. in Computer Science').")
    institution: str = Field(..., description="University or institution name.")
    period: str | None = Field(None, description="e.g. '2016–2020' or 'September 2016 – June 2020'.")
    key_topics: list[str] | None = Field(None, description="Relevant coursework or thesis topics.")


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
    event: str | None = Field(None, description="Event or organization.")
    year: str | None = Field(None, description="Year received.")


class LanguageEntry(BaseModel):
    """Language proficiency."""

    language: str = Field(..., description="Language name (e.g. 'English', 'Danish').")
    proficiency: str = Field(..., description="Level: 'native', 'fluent', 'advanced', 'intermediate', 'basic'.")


class ReferenceEntry(BaseModel):
    """Professional reference."""

    name: str = Field(..., description="Reference name.")
    title: str | None = Field(None, description="Job title.")
    company: str | None = Field(None, description="Company.")
    email: str | None = Field(None, description="Email address.")
    phone: str | None = Field(None, description="Phone number.")


# ── Skills taxonomy ────────────────────────────────────────────────────


class SkillsSection(BaseModel):
    """Grouped skills — matches the CandidateProfile structure."""

    programming_ml: list[Skill] | None = Field(None, description="Programming languages and ML frameworks.")
    domain_expertise: list[str] | None = Field(None, description="Domain expertise areas (plain strings).")
    software_tools: list[str] | None = Field(None, description="Software tools (plain strings).")
    languages: list[LanguageEntry] | None = Field(None, description="Human languages.")


# ── Cover letter ──────────────────────────────────────────────────────


class CoverLetter(BaseModel):
    """Structured cover letter."""

    opening_paragraph: str = Field(..., description="Salutation + hook. Addresses hiring manager if known.")
    body_paragraphs: list[str] = Field(
        ...,
        description="2–4 body paragraphs. Each highlights fit, skills, or achievements.",
        max_length=4,
    )
    company_connection_paragraph: str | None = Field(
        None,
        description="Optional paragraph connecting to the company's mission or recent work.",
    )
    personal_fit_paragraph: str | None = Field(
        None,
        description="Why the candidate is a good cultural/personal fit.",
    )
    closing_paragraph: str = Field(..., description="Call to action + thank you.")


# ── Metadata / tracking ───────────────────────────────────────────────


class IncorporatedKeyword(BaseModel):
    """Tracks how a job keyword was incorporated into the CV."""

    keyword: str = Field(..., description="The job-posting keyword.")
    where_incorporated: str = Field(..., description="Section and context (e.g. 'experience: ML Engineer bullet 2').")
    original_context: str | None = Field(None, description="How the candidate's profile supported this keyword.")


class AddressedRedFlag(BaseModel):
    """Tracks how a red flag was addressed in the CV."""

    red_flag: str = Field(..., description="The red flag identified during ranking.")
    how_addressed: str = Field(..., description="How the CV addresses it (reframing, emphasis, etc.).")


# ── Full CV document ──────────────────────────────────────────────────


class CV(BaseModel):
    """Complete structured CV document — the single source of truth for rendering.

    All sections are optional so that the same schema handles profiles
    with different section combinations (e.g. no publications, no awards).
    """

    # ── Header ─────────────────────────────────────────────────────
    first_name: str = Field(..., description="Candidate first name.")
    last_name: str = Field(..., description="Candidate last name.")
    email: str = Field(..., description="Email address.")
    phone: str | None = Field(None, description="Phone number (international format).")
    linkedin: str | None = Field(None, description="Full LinkedIn URL.")
    github: str | None = Field(None, description="Full GitHub URL.")
    address: str | None = Field(None, description="City and country (e.g. 'Copenhagen, Denmark').")
    location: str | None = Field(None, description="Current location, same as address if not specified separately.")

    # ── Profile ────────────────────────────────────────────────────
    profile_statement: str | None = Field(
        None,
        description="2–3 sentence summary targeting the role. Mentions job title.",
    )

    # ── Core competencies (rendered as tag cloud / grouped list) ───
    core_competencies: list[str] | None = Field(
        None,
        description="Key skills / competencies to highlight (8–15 items).",
    )

    # ── Skills (detailed, with proficiency) ────────────────────────
    skills: SkillsSection | None = Field(None, description="Detailed skills grouped by category.")

    # ── Experience ─────────────────────────────────────────────────
    experience: list[ExperienceEntry] = Field(
        ...,
        description="Tailored professional experience entries (max ~6).",
        max_length=6,
    )

    # ── Education ──────────────────────────────────────────────────
    education: list[EducationEntry] | None = Field(None, description="Education background (max ~3 entries).")

    # ── Optional sections ──────────────────────────────────────────
    publications: list[PublicationEntry] | None = Field(None, description="Publications (max ~10).")
    awards: list[AwardEntry] | None = Field(None, description="Honors and awards (max ~5).")
    references: list[ReferenceEntry] | None = Field(None, description="Professional references (max ~3).")

    # ── Cover letter ───────────────────────────────────────────────
    cover_letter: CoverLetter | None = Field(None, description="Tailored cover letter.")

    # ── Metadata (not rendered) ────────────────────────────────────
    incorporated_keywords: list[IncorporatedKeyword] | None = Field(
        None,
        description="Keywords from the job posting incorporated into the CV.",
    )
    addressed_red_flags: list[AddressedRedFlag] | None = Field(
        None,
        description="Red flags from ranking that were addressed.",
    )


# ── Drafter LLM output (wraps CV for structured generation) ──────────


class DrafterOutput(BaseModel):
    """Output schema for the drafter LLM call."""

    cv: CV = Field(..., description="The complete tailored CV and cover letter.")

    class Config:
        json_schema_extra = {
            "description": "Generate a complete CV document in JSON format.",
        }
