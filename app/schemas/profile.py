"""Pydantic schemas for candidate profile management (setup skill).

These are the API boundary — never expose ORM models directly.
"""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, field_validator

# ── Validators ──────────────────────────────────────────────────────

PHONE_REGEX = re.compile(r"^\+?[1-9]\d{1,14}$")  # E.164 format
DATE_REGEX = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")  # YYYY-MM or YYYY-MM-DD
LINKEDIN_URL_REGEX = re.compile(r"^https?://(www\.)?linkedin\.com/in/[\w-]+/?$")
GITHUB_URL_REGEX = re.compile(r"^https?://(www\.)?github\.com/[\w-]+/?$")


def validate_phone(v: str | None) -> str | None:
    if v is None:
        return None
    # Remove spaces and common separators
    cleaned = re.sub(r"[\s\-\(\)]", "", v)
    if not PHONE_REGEX.match(cleaned):
        raise ValueError("Phone must be in E.164 format (e.g., +4512345678)")
    return cleaned


def validate_date_format(v: str | None) -> str | None:
    if v is None:
        return None
    if not DATE_REGEX.match(v):
        raise ValueError("Date must be in YYYY-MM or YYYY-MM-DD format")
    return v


def validate_linkedin_url(v: str | None) -> str | None:
    if v is None:
        return None
    if not LINKEDIN_URL_REGEX.match(v):
        raise ValueError("LinkedIn URL must be in format https://linkedin.com/in/username")
    return v


def validate_github_url(v: str | None) -> str | None:
    if v is None:
        return None
    if not GITHUB_URL_REGEX.match(v):
        raise ValueError("GitHub URL must be in format https://github.com/username")
    return v


# ── Nested models ───────────────────────────────────────────────────


class LanguageEntry(BaseModel):
    language: str
    proficiency: str  # Native, Fluent, Advanced, Intermediate, Basic


class EducationEntry(BaseModel):
    degree: str
    institution: str
    start_date: str | None = None
    end_date: str | None = None
    key_topics: str | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, v):
        return validate_date_format(v)


class ExperienceBullet(BaseModel):
    title: str
    company: str
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None
    client_context: str | None = None
    technologies: list[str] = []
    bullets: list[str] = []

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, v):
        return validate_date_format(v)


class ProjectEntry(BaseModel):
    name: str
    description: str | None = None
    role: str | None = None
    client: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    technologies: list[str] = []
    url: str | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, v):
        return validate_date_format(v)


class CertificationEntry(BaseModel):
    name: str
    issuer: str
    issue_date: str | None = None
    expiration_date: str | None = None
    credential_id: str | None = None
    credential_url: str | None = None


class SkillCategory(BaseModel):
    programming_ml: list[dict[str, Any]] = []
    domain_expertise: list[str] = []
    software_tools: list[str] = []


class PublicationEntry(BaseModel):
    authors: str | None = None
    year: str | None = None
    title: str
    journal: str | None = None
    doi: str | None = None


class AwardEntry(BaseModel):
    award: str
    event: str | None = None
    year: str | None = None


class ReferenceEntry(BaseModel):
    name: str
    title: str | None = None
    company: str | None = None
    email: EmailStr | None = None
    phone: str | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v):
        return validate_phone(v)


# ── Job Target schema ────────────────────────────────────────────


class JobTarget(BaseModel):
    """Structured job search criteria the candidate wants."""

    target_titles: list[str] = []
    seniority: str | None = None  # junior | mid | senior | lead | manager | director | executive
    work_mode: list[str] = []  # remote | hybrid | onsite
    search_locations: list[str] = []
    search_radius_km: int | None = None
    employment_types: list[str] = []  # full-time | part-time | contract | freelance | internship
    industry: str | None = None
    keywords: list[str] = []
    exclude_keywords: list[str] = []
    exclude_companies: list[str] = []
    salary_min: int | None = None
    salary_max: int | None = None
    availability: str | None = None  # immediate | within_month | exploring
    visa_needed: bool | None = None
    relocation_willing: bool | None = None


# ── Request schemas ─────────────────────────────────────────────────


class CandidateProfileCreate(BaseModel):
    """Create or fully replace a candidate profile."""

    full_name: str | None = None
    location: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    languages: list[LanguageEntry] | None = None
    employment_status: str | None = None
    constraints: str | None = None

    education: list[EducationEntry] | None = None
    experience: list[ExperienceBullet] | None = None
    certifications: list[CertificationEntry] | None = None
    projects: list[ProjectEntry] | None = None
    skills: SkillCategory | None = None
    publications: list[PublicationEntry] | None = None
    awards: list[AwardEntry] | None = None
    references: list[ReferenceEntry] | None = None

    profile_statement: str | None = None
    job_target: JobTarget | None = None
    setup_method: str | None = None  # "documents", "cv_import", "interview"

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v):
        return validate_phone(v)

    @field_validator("linkedin_url", mode="before")
    @classmethod
    def validate_linkedin(cls, v):
        return validate_linkedin_url(v)

    @field_validator("github_url", mode="before")
    @classmethod
    def validate_github(cls, v):
        return validate_github_url(v)


class CandidateProfileUpdate(BaseModel):
    """Partial update — only provided fields are changed."""

    full_name: str | None = None
    location: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    languages: list[LanguageEntry] | None = None
    employment_status: str | None = None
    constraints: str | None = None

    education: list[EducationEntry] | None = None
    experience: list[ExperienceBullet] | None = None
    certifications: list[CertificationEntry] | None = None
    projects: list[ProjectEntry] | None = None
    skills: SkillCategory | None = None
    publications: list[PublicationEntry] | None = None
    awards: list[AwardEntry] | None = None
    references: list[ReferenceEntry] | None = None

    profile_statement: str | None = None
    job_target: JobTarget | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v):
        return validate_phone(v)

    @field_validator("linkedin_url", mode="before")
    @classmethod
    def validate_linkedin(cls, v):
        return validate_linkedin_url(v)

    @field_validator("github_url", mode="before")
    @classmethod
    def validate_github(cls, v):
        return validate_github_url(v)


# ── Response schemas ────────────────────────────────────────────────


class CandidateProfileOut(BaseModel):
    """Profile response — never expose internal IDs unless needed."""

    id: str
    full_name: str | None
    location: str | None
    phone: str | None
    email: str | None
    linkedin_url: str | None
    github_url: str | None
    portfolio_url: str | None = None
    languages: list[LanguageEntry] | None
    employment_status: str | None
    constraints: str | None

    education: list[EducationEntry] | None
    experience: list[ExperienceBullet] | None
    certifications: list[CertificationEntry] | None = None
    projects: list[ProjectEntry] | None
    skills: SkillCategory | None
    publications: list[PublicationEntry] | None
    awards: list[AwardEntry] | None
    references: list[ReferenceEntry] | None

    profile_statement: str | None
    job_target: JobTarget | None = None
    setup_method: str | None
    setup_completed_at: datetime | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProfileSummaryOut(BaseModel):
    """Lightweight profile summary for listing / quick checks."""

    id: str
    full_name: str | None
    email: str | None
    location: str | None
    setup_method: str | None
    setup_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Behavioral profile schemas ──────────────────────────────────────


class BehavioralDrive(BaseModel):
    drive: str
    level: str | None = None
    meaning: str | None = None


class BehavioralBehavior(BaseModel):
    behavior: str
    description: str | None = None


class GrowthArea(BaseModel):
    area: str
    positive_frame: str | None = None


class ManagementPreferences(BaseModel):
    works_with: list[str] = []
    doesnt_work: list[str] = []


class BehavioralProfileCreate(BaseModel):
    profile_type: str | None = None
    summary: str | None = None
    drives: list[BehavioralDrive] | None = None
    behaviors: list[BehavioralBehavior] | None = None
    work_preferences: list[str] | None = None
    growth_areas: list[GrowthArea] | None = None
    strong_fit_keywords: list[str] | None = None
    friction_keywords: list[str] | None = None
    management_preferences: ManagementPreferences | None = None


class BehavioralProfileOut(BaseModel):
    id: str
    candidate_id: str
    profile_type: str | None
    summary: str | None
    drives: list[BehavioralDrive] | None
    behaviors: list[BehavioralBehavior] | None
    work_preferences: list[str] | None
    growth_areas: list[GrowthArea] | None
    strong_fit_keywords: list[str] | None
    friction_keywords: list[str] | None
    management_preferences: ManagementPreferences | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── STAR example schemas ────────────────────────────────────────────


class StarExampleCreate(BaseModel):
    title: str
    skill_demonstrated: str | None = None
    situation: str
    task: str
    action: str
    result: str
    use_for: list[str] | None = None


class StarExampleOut(BaseModel):
    id: str
    candidate_id: str
    title: str
    skill_demonstrated: str | None
    situation: str
    task: str
    action: str
    result: str
    use_for: list[str] | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
