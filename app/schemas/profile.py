"""Pydantic schemas for candidate profile management (setup skill).

These are the API boundary — never expose ORM models directly.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ── Nested models ───────────────────────────────────────────────────


class LanguageEntry(BaseModel):
    language: str
    proficiency: str  # Native, Fluent, Advanced, Intermediate, Basic


class EducationEntry(BaseModel):
    degree: str
    period: str | None = None
    institution: str
    key_topics: str | None = None


class ExperienceBullet(BaseModel):
    title: str
    company: str
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None
    bullets: list[str] = []


class ProjectEntry(BaseModel):
    name: str
    description: str | None = None


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
    email: str | None = None
    phone: str | None = None


# ── Request schemas ─────────────────────────────────────────────────


class CandidateProfileCreate(BaseModel):
    """Create or fully replace a candidate profile."""

    full_name: str | None = None
    location: str | None = None
    phone: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    languages: list[LanguageEntry] | None = None
    employment_status: str | None = None
    constraints: str | None = None

    education: list[EducationEntry] | None = None
    experience: list[ExperienceBullet] | None = None
    projects: list[ProjectEntry] | None = None
    skills: SkillCategory | None = None
    publications: list[PublicationEntry] | None = None
    awards: list[AwardEntry] | None = None
    references: list[ReferenceEntry] | None = None

    profile_statement: str | None = None
    setup_method: str | None = None  # "documents", "cv_import", "interview"


class CandidateProfileUpdate(BaseModel):
    """Partial update — only provided fields are changed."""

    full_name: str | None = None
    location: str | None = None
    phone: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    languages: list[LanguageEntry] | None = None
    employment_status: str | None = None
    constraints: str | None = None

    education: list[EducationEntry] | None = None
    experience: list[ExperienceBullet] | None = None
    projects: list[ProjectEntry] | None = None
    skills: SkillCategory | None = None
    publications: list[PublicationEntry] | None = None
    awards: list[AwardEntry] | None = None
    references: list[ReferenceEntry] | None = None

    profile_statement: str | None = None


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
    languages: list[LanguageEntry] | None
    employment_status: str | None
    constraints: str | None

    education: list[EducationEntry] | None
    experience: list[ExperienceBullet] | None
    projects: list[ProjectEntry] | None
    skills: SkillCategory | None
    publications: list[PublicationEntry] | None
    awards: list[AwardEntry] | None
    references: list[ReferenceEntry] | None

    profile_statement: str | None
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