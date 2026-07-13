"""SQLAlchemy ORM base, mixins, and all domain models.

Models are organised by domain area.  JSON columns use PostgreSQL JSONB
for nested profile data (education, experience, skills, etc.).
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


class TimestampMixin:
    """Add created_at / updated_at columns to any model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


# ── Helpers ────────────────────────────────────────────────────────


def new_uuid() -> str:
    return str(uuid.uuid4())


# ═══════════════════════════════════════════════════════════════════
# USERS & AUTH
# ═══════════════════════════════════════════════════════════════════


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)

    # Active LLM provider (default from settings, overridable per user)
    active_provider: Mapped[str] = mapped_column(String(50), default="anthropic")

    # Relationships
    provider_credentials: Mapped[list["ProviderCredential"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    candidate_profile: Mapped["CandidateProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class ProviderCredential(Base, TimestampMixin):
    """Encrypted API key per LLM provider for a user.

    The api_key field stores the value encrypted at rest (application-level
    encryption, not just TLS).  The encryption/decryption happens in the
    service layer, not in the model.
    """

    __tablename__ = "provider_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # anthropic, openai, nvidia_nim, lm_studio
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_base: Mapped[str | None] = mapped_column(String(500))  # For self-hosted providers

    user: Mapped["User"] = relationship(back_populates="provider_credentials")


# ═══════════════════════════════════════════════════════════════════
# CANDIDATE PROFILE  (maps to 01-candidate-profile.md)
# ═══════════════════════════════════════════════════════════════════


class CandidateProfile(Base, TimestampMixin):
    """Main candidate profile — identity, education, experience, skills.

    Nested data (education entries, work history, skills) is stored as
    JSONB arrays so the schema stays flexible without migrations for
    every new field.  The Pydantic schemas in app/schemas/ validate
    the shape at the API boundary.
    """

    __tablename__ = "candidate_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # ── Identity ──────────────────────────────────────────────
    full_name: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    github_url: Mapped[str | None] = mapped_column(String(500))
    languages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)  # [{"language": "...", "proficiency": "..."}]
    employment_status: Mapped[str | None] = mapped_column(String(100))
    constraints: Mapped[str | None] = mapped_column(Text)

    # ── Education ─────────────────────────────────────────────
    # [{"degree": "...", "period": "...", "institution": "...", "key_topics": "..."}]
    education: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Professional Experience ───────────────────────────────
    # [{"title": "...", "company": "...", "start_date": "...", "end_date": "...",
    #   "location": "...", "bullets": ["...", "..."]}]
    experience: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Independent Projects ──────────────────────────────────
    # [{"name": "...", "description": "..."}]
    projects: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Technical Skills ──────────────────────────────────────
    # {"programming_ml": [{"language": "...", "proficiency": "...", "frameworks": ["..."]}],
    #  "domain_expertise": ["..."],
    #  "software_tools": ["..."]}
    skills: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # ── Publications ──────────────────────────────────────────
    # [{"authors": "...", "year": "...", "title": "...", "journal": "...", "doi": "..."}]
    publications: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Awards ────────────────────────────────────────────────
    # [{"award": "...", "event": "...", "year": "..."}]
    awards: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── References ────────────────────────────────────────────
    # [{"name": "...", "title": "...", "company": "...", "email": "...", "phone": "..."}]
    references: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Profile statement (generated / tailored) ──────────────
    profile_statement: Mapped[str | None] = mapped_column(Text)

    # ── Setup method ──────────────────────────────────────────
    setup_method: Mapped[str | None] = mapped_column(String(20))  # "documents", "cv_import", "interview"
    setup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="candidate_profile")


# ═══════════════════════════════════════════════════════════════════
# BEHAVIORAL PROFILE  (maps to 02-behavioral-profile.md)
# ═══════════════════════════════════════════════════════════════════


class BehavioralProfile(Base, TimestampMixin):
    """Behavioral assessment — PI/DISC/StrengthsFinder results.

    One-to-one with candidate_profiles.
    """

    __tablename__ = "behavioral_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    profile_type: Mapped[str | None] = mapped_column(String(100))  # e.g. "Analytical Driver"
    summary: Mapped[str | None] = mapped_column(Text)

    # Core drives: [{"drive": "...", "level": "...", "meaning": "..."}]
    drives: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # Strongest behaviors: [{"behavior": "...", "description": "..."}]
    behaviors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # Work preferences: ["...", "..."]
    work_preferences: Mapped[list[str] | None] = mapped_column(JSONB)

    # Growth areas: [{"area": "...", "positive_frame": "..."}]
    growth_areas: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # Keywords that indicate strong fit: ["...", "..."]
    strong_fit_keywords: Mapped[list[str] | None] = mapped_column(JSONB)

    # Keywords that indicate friction: ["...", "..."]
    friction_keywords: Mapped[list[str] | None] = mapped_column(JSONB)

    # Management style preferences: {"works_with": ["..."], "doesnt_work": ["..."]}
    management_preferences: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# ═══════════════════════════════════════════════════════════════════
# STAR EXAMPLES  (maps to 07-interview-prep.md)
# ═══════════════════════════════════════════════════════════════════


class StarExample(Base, TimestampMixin):
    """A single STAR-format example for interview preparation."""

    __tablename__ = "star_examples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(255))  # e.g. "ML Pipeline Optimization"
    skill_demonstrated: Mapped[str | None] = mapped_column(String(255))

    situation: Mapped[str] = mapped_column(Text)
    task: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)

    # Question types this example is useful for: ["teamwork", "technical challenge", ...]
    use_for: Mapped[list[str] | None] = mapped_column(JSONB)


# ═══════════════════════════════════════════════════════════════════
# JOB POSTINGS  (results from scraping)
# ═══════════════════════════════════════════════════════════════════


class JobPosting(Base, TimestampMixin):
    """A job posting discovered by a scraper.

    Deduplicated by (portal, external_id) — the same job from the same
    portal is only stored once.  The ``status`` field tracks the lifecycle:
    new → ranked → applied → expired.
    """

    __tablename__ = "job_postings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # ── Source ────────────────────────────────────────────────
    portal: Mapped[str] = mapped_column(String(50), nullable=False)  # linkedin, jobindex, freehire, ...
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)  # id from the portal

    # ── Posting data ─────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(1000))
    posting_date: Mapped[str | None] = mapped_column(String(20))  # YYYY-MM-DD
    deadline: Mapped[str | None] = mapped_column(String(20))

    # Full posting text (from detail fetch)
    description: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[list[str] | None] = mapped_column(JSONB)
    employment_type: Mapped[str | None] = mapped_column(String(50))  # full-time, part-time, ...

    # ── Language ─────────────────────────────────────────────
    language: Mapped[str | None] = mapped_column(String(10))  # en, da, ...

    # ── Lifecycle ─────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="new")  # new, ranked, applied, expired
    rank_score: Mapped[float | None] = mapped_column(default=None)
    rank_verdict: Mapped[str | None] = mapped_column(String(50))
    rank_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Raw scraper output (for debugging / re-parsing) ──────
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ScrapeRun(Base, TimestampMixin):
    """History of scraper executions (manual or scheduled).

    Tracks which portals were queried, how many results came back, and
    whether the run succeeded or failed.
    """

    __tablename__ = "scrape_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # ── Run metadata ─────────────────────────────────────────
    triggered_by: Mapped[str] = mapped_column(String(20), nullable=False)  # "manual" or "scheduler"
    focus_area: Mapped[str | None] = mapped_column(String(255))  # e.g. "data science"
    broad: Mapped[bool] = mapped_column(default=False)

    # ── Results ───────────────────────────────────────────────
    portals_queried: Mapped[list[str]] = mapped_column(JSONB)  # ["linkedin", "jobindex", ...]
    jobs_found: Mapped[int] = mapped_column(default=0)
    jobs_new: Mapped[int] = mapped_column(default=0)  # after dedup
    jobs_expired: Mapped[int] = mapped_column(default=0)

    # ── Status ────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="running")  # running, completed, failed
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ═══════════════════════════════════════════════════════════════════
# RANK EVALUATION  (detailed breakdown from /rank)
# ═══════════════════════════════════════════════════════════════════


class RankEvaluation(Base, TimestampMixin):
    """Detailed rank evaluation for a job posting.

    Stores the breakdown of scores and LLM-generated insights so they
    can be retrieved without re-running the LLM.
    """

    __tablename__ = "rank_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_posting_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("job_postings.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # ── Dimension scores (0-100) ──────────────────────────────
    technical_score: Mapped[int] = mapped_column(default=0)
    experience_score: Mapped[int] = mapped_column(default=0)
    behavioral_score: Mapped[int] = mapped_column(default=0)
    career_score: Mapped[int] = mapped_column(default=0)

    # ── Overall ───────────────────────────────────────────────
    overall_score: Mapped[int] = mapped_column(default=0)
    verdict: Mapped[str] = mapped_column(String(50))  # Strong Fit, Good Fit, Moderate Fit, Weak Fit, Poor Fit

    # ── Location ──────────────────────────────────────────────
    location_status: Mapped[str] = mapped_column(String(20))  # PASS, FAIL, FLAG

    # ── Deadline ──────────────────────────────────────────────
    deadline: Mapped[str | None] = mapped_column(String(20))
    deadline_urgent: Mapped[bool] = mapped_column(default=False)

    # ── LLM-generated insights ────────────────────────────────
    strengths: Mapped[list[str] | None] = mapped_column(JSONB)  # max 3
    gaps: Mapped[list[str] | None] = mapped_column(JSONB)  # max 3
    missing_keywords: Mapped[list[str] | None] = mapped_column(JSONB)  # max 5
    red_flags: Mapped[list[str] | None] = mapped_column(JSONB)  # max 3

    # ── Language ──────────────────────────────────────────────
    language: Mapped[str | None] = mapped_column(String(10))

    # ── Raw LLM response (for debugging) ──────────────────────
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # ── Relationships ─────────────────────────────────────────
    job_posting: Mapped["JobPosting"] = relationship(backref="rank_evaluation")


# ═══════════════════════════════════════════════════════════════════
# APPLICATION  (generated CV + cover letter from /apply)
# ═══════════════════════════════════════════════════════════════════


class Application(Base, TimestampMixin):
    """A generated job application (tailored CV + cover letter).

    Created by the /apply workflow after a successful rank evaluation.
    Stores the tailored content and paths to generated PDFs.
    """

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False
    )
    rank_evaluation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rank_evaluations.id", ondelete="CASCADE"), nullable=False
    )

    # ── Tailored content ──────────────────────────────────────
    # The rewritten experience section using X-Y-Z formula
    tailored_experience: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Missing keywords that were incorporated (and where)
    incorporated_keywords: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Red flags that were addressed
    addressed_red_flags: Mapped[list[str] | None] = mapped_column(JSONB)

    # ── Generated files ───────────────────────────────────────
    cv_tex_path: Mapped[str | None] = mapped_column(String(500))
    cv_pdf_path: Mapped[str | None] = mapped_column(String(500))
    cover_letter_tex_path: Mapped[str | None] = mapped_column(String(500))
    cover_letter_pdf_path: Mapped[str | None] = mapped_column(String(500))

    # ── Compilation status ────────────────────────────────────
    cv_compiled: Mapped[bool] = mapped_column(default=False)
    cv_pages: Mapped[int | None] = mapped_column
    cover_letter_compiled: Mapped[bool] = mapped_column(default=False)
    cover_letter_pages: Mapped[int | None] = mapped_column

    # ── Metadata ──────────────────────────────────────────────
    cv_template: Mapped[str] = mapped_column(String(100), default="moderncv-banking")
    cover_letter_template: Mapped[str] = mapped_column(String(100), default="cover-cls")
    language: Mapped[str] = mapped_column(String(10), default="en")  # matches job posting language

    # ── Relationships ─────────────────────────────────────────
    job_posting: Mapped["JobPosting"] = relationship(backref="applications")
    rank_evaluation: Mapped["RankEvaluation"] = relationship(backref="applications")


# ═══════════════════════════════════════════════════════════════════
# INTERVIEW PREP  (maps to /interview command)
# ═══════════════════════════════════════════════════════════════════


class InterviewPrep(Base, TimestampMixin):
    """Interview preparation pack for a specific application and stage.

    Created by the /interview workflow. Contains likely questions, STAR
    mappings, consistency brief, tough questions, questions to ask, and
    logistics. One per application per stage.
    """

    __tablename__ = "interview_preps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )

    # ── Stage info ────────────────────────────────────────────
    stage: Mapped[str] = mapped_column(String(50))  # phone_screen, technical, case, final_round
    interview_date: Mapped[str | None] = mapped_column(String(20))  # YYYY-MM-DD
    interview_format: Mapped[str | None] = mapped_column(String(20))  # phone, video, onsite
    interviewer_names: Mapped[list[str] | None] = mapped_column(JSONB)

    # ── Company research (interview-focused) ──────────────────
    company_research: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Verified conversation hooks: [{"topic": "...", "source_url": "..."}]
    conversation_hooks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Likely questions ──────────────────────────────────────
    # [{"question": "...", "source": "feedback|gaps|requirements|stage", "priority": "high|medium|low"}]
    likely_questions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── STAR answer mapping ───────────────────────────────────
    # [{"question": "...", "star_example_id": "...", "star_example_title": "..."}]
    star_mapping: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── New STAR drafts (for questions not covered by existing examples) ────
    # [{"question": "...", "draft_situation": "...", "draft_task": "...", "draft_action": "...", "draft_result": "..."}]
    new_star_drafts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Consistency brief ─────────────────────────────────────
    # Claims from submitted CV/cover letter that interviewer will probe
    consistency_brief: Mapped[list[str] | None] = mapped_column(JSONB)

    # ── Tough questions (customized) ──────────────────────────
    # [{"question": "...", "answer": "..."}]
    tough_questions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Questions to ask interviewer ──────────────────────────
    # [{"question": "...", "category": "role|team|tech|culture", "why_ask": "..."}]
    questions_to_ask: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Logistics ─────────────────────────────────────────────
    logistics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # ── Mock interview transcript (optional) ──────────────────
    mock_transcript: Mapped[str | None] = mapped_column(Text)

    # ── Raw LLM response (for debugging) ──────────────────────
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # ── Relationships ─────────────────────────────────────────
    application: Mapped["Application"] = relationship(backref="interview_preps")


# ═══════════════════════════════════════════════════════════════════
# OUTCOME  (maps to /outcome command)
# ═══════════════════════════════════════════════════════════════════


class Outcome(Base, TimestampMixin):
    """Record of an application outcome — progress updates and final resolutions.

    Created/updated by the /outcome workflow. Tracks the lifecycle of an
    application from applied through to final resolution (hired, rejected,
    no response, etc.). Feeds back into /setup calibration and STAR mining.
    """

    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )

    # ── Status ──────────────────────────────────────────────────
    # Progress updates (application still open):
    #   interview_invited, phone_screen_completed, technical_completed,
    #   case_completed, final_round_completed, offer_received
    # Resolutions (application closed):
    #   hired, offer_declined, rejected, no_response, interview_only, withdrawn
    status: Mapped[str] = mapped_column(String(50), nullable=False)

    # ── Date resolved ───────────────────────────────────────────
    # Only set when status is a final resolution (not a progress update)
    date_resolved: Mapped[str | None] = mapped_column(String(20))  # YYYY-MM-DD

    # ── Interview stages reached ────────────────────────────────
    # Checkboxes with dates, updated as stages are completed
    phone_screen_date: Mapped[str | None] = mapped_column(String(20))
    technical_date: Mapped[str | None] = mapped_column(String(20))
    case_date: Mapped[str | None] = mapped_column(String(20))
    final_round_date: Mapped[str | None] = mapped_column(String(20))
    offer_received_date: Mapped[str | None] = mapped_column(String(20))

    # ── Notes ───────────────────────────────────────────────────
    # Feedback received, what to do differently, signals about what
    # the company valued — appended per update, never overwritten
    notes: Mapped[str | None] = mapped_column(Text)

    # ── What would you do differently ───────────────────────────
    # Candidate's reflection on the process — feeds /setup calibration
    # and STAR mining
    lessons_learned: Mapped[str | None] = mapped_column(Text)

    # ── Signals about what the company valued ───────────────────
    # Concrete observations from the process — feeds /setup calibration
    valued_signals: Mapped[list[str] | None] = mapped_column(JSONB)

    # ── Relationships ───────────────────────────────────────────
    application: Mapped["Application"] = relationship(backref="outcomes")


# ═══════════════════════════════════════════════════════════════════
# COMPETENCY EXPANSION  (maps to /expand command)
# ═══════════════════════════════════════════════════════════════════


class CompetencyExpansion(Base, TimestampMixin):
    """Record of a competency expansion run — discovers hidden skills from documents and online presence.

    Created by the /expand workflow. Scans documents (CV, LinkedIn, diplomas, references),
    GitHub profile, and other URLs in the candidate profile. For each "experience item"
    found, searches the web to extract implied competencies (direct lookup + inference).
    Additive only — never modifies existing profile content, only extends it.
    """

    __tablename__ = "competency_expansions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False
    )

    # ── Sources scanned ──────────────────────────────────────────
    # Which document folders were scanned
    scanned_cv: Mapped[bool] = mapped_column(default=False)
    scanned_linkedin: Mapped[bool] = mapped_column(default=False)
    scanned_diplomas: Mapped[bool] = mapped_column(default=False)
    scanned_references: Mapped[bool] = mapped_column(default=False)
    scanned_github: Mapped[bool] = mapped_column(default=False)
    scanned_other_urls: Mapped[bool] = mapped_column(default=False)

    # ── Discovered experience items ──────────────────────────────
    # Each item represents something that implies skills/competencies
    # [{"source": "cv|linkedin|diplomas|references|github|other_url",
    #   "type": "course|certification|job_bullet|project|volunteer|repo",
    #   "title": "...", "description": "...", "date": "...", "source_file": "..."}]
    experience_items: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Web-enriched competencies ────────────────────────────────
    # For each experience item, the competencies discovered via web search
    # [{"experience_item_id": "...", "competencies": ["...", "..."],
    #   "source": "direct_lookup|inferred", "source_urls": ["..."]}]
    enriched_competencies: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Proposed additions to profile ────────────────────────────
    # Competencies ready to be added to the candidate profile (pending user approval)
    # [{"category": "programming_ml|domain_expertise|software_tools",
    #   "skill": "...", "proficiency": "...", "evidence": "...", "source": "..."}]
    proposed_additions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Status ───────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, completed, failed
    error_message: Mapped[str | None] = mapped_column(Text)

    # ── Raw LLM response (for debugging) ─────────────────────────
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # ── Relationships ────────────────────────────────────────────
    candidate: Mapped["CandidateProfile"] = relationship(backref="competency_expansions")


# ═══════════════════════════════════════════════════════════════════
# UPSKILL  (maps to /upskill command)
# ═══════════════════════════════════════════════════════════════════


class Upskill(Base, TimestampMixin):
    """Record of an upskill analysis run — identifies skill gaps from tracked jobs and generates a learning plan.

    Created by the /upskill workflow. Analyses jobs in job_search_tracker.csv (or a single job URL)
    against the candidate profile to identify skill gaps, then produces a heatmap of those gaps
    and a prioritized learning plan with concrete, web-searched study resources and a recommended
    study order.
    """

    __tablename__ = "upskills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False
    )

    # ── Mode ──────────────────────────────────────────────────────
    # "aggregate" = analyse all jobs in job_search_tracker.csv
    # "targeted"  = analyse a single job posting URL
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # "aggregate" | "targeted"

    # ── Target job (for targeted mode) ───────────────────────────
    target_job_posting_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("job_postings.id", ondelete="SET NULL")
    )
    target_job_url: Mapped[str | None] = mapped_column(String(1000))

    # ── Hard skill gaps (Pass 1) ──────────────────────────────────
    # [{"skill": "...", "type": "hard", "priority": "Critical|High|Medium|Low",
    #   "source_jobs": ["job_id1", "job_id2"], "frequency": 3, "fit_weight": 2.5}]
    hard_skill_gaps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Synthesized gaps (Pass 2) ─────────────────────────────────
    # [{"skill": "...", "type": "domain|soft|tooling|credential", "priority": "Critical|High|Medium|Low",
    #   "source": "LLM synthesis", "evidence": "..."}]
    synthesized_gaps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Combined gap heatmap ──────────────────────────────────────
    # [{"skill": "...", "type": "hard|domain|soft|tooling|credential", "priority": "Critical|High|Medium|Low",
    #   "gap_source": "4/5 jobs, score 3.2 | LLM synthesis"}]
    gap_heatmap: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Learning plan ─────────────────────────────────────────────
    # [{"skill": "...", "type": "...", "priority": "...",
    #   "resources": [{"title": "...", "url": "...", "format": "course|video|article|certification",
    #                 "duration_hours": 10, "cost": "free|paid", "quality_score": 8}],
    #   "study_order": 1, "prerequisites": ["..."], "estimated_weeks": 4}]
    learning_plan: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # ── Status ────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, completed, failed
    error_message: Mapped[str | None] = mapped_column(Text)

    # ── Raw LLM response (for debugging) ──────────────────────────
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # ── Relationships ─────────────────────────────────────────────
    candidate: Mapped["CandidateProfile"] = relationship(backref="upskills")
    target_job_posting: Mapped["JobPosting | None"] = relationship()