"""SQLAlchemy ORM base, mixins, and all domain models.

Models are organised by domain area.  JSON columns use PostgreSQL JSONB
for nested profile data (education, experience, skills, etc.).
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Cross-engine JSON type: JSONB on PostgreSQL, plain JSON on SQLite (tests)
FlexJSON = JSON().with_variant(JSONB(), "postgresql")


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
    model_selections: Mapped[list["UserModelSelection"]] = relationship(
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


class UserModelSelection(Base, TimestampMixin):
    """The model a user has selected for a given LLM provider.

    Decoupled from ProviderCredential so that selecting a model does not
    require touching the encrypted API key.  One row per (user, provider).
    """

    __tablename__ = "user_model_selection"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    user: Mapped["User"] = relationship(back_populates="model_selections")

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_model_selection_user_provider"),
    )


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
    languages: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)  # [{"language": "...", "proficiency": "..."}]
    employment_status: Mapped[str | None] = mapped_column(String(100))
    constraints: Mapped[str | None] = mapped_column(Text)

    # ── Education ─────────────────────────────────────────────
    # [{"degree": "...", "period": "...", "institution": "...", "key_topics": "..."}]
    education: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Professional Experience ───────────────────────────────
    # [{"title": "...", "company": "...", "start_date": "...", "end_date": "...",
    #   "location": "...", "bullets": ["...", "..."]}]
    experience: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Independent Projects ──────────────────────────────────
    # [{"name": "...", "description": "..."}]
    projects: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Technical Skills ──────────────────────────────────────
    # {"programming_ml": [{"language": "...", "proficiency": "...", "frameworks": ["..."]}],
    #  "domain_expertise": ["..."],
    #  "software_tools": ["..."]}
    skills: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)

    # ── Publications ──────────────────────────────────────────
    # [{"authors": "...", "year": "...", "title": "...", "journal": "...", "doi": "..."}]
    publications: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Awards ────────────────────────────────────────────────
    # [{"award": "...", "event": "...", "year": "..."}]
    awards: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── References ────────────────────────────────────────────
    # [{"name": "...", "title": "...", "company": "...", "email": "...", "phone": "..."}]
    references: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

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
    drives: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # Strongest behaviors: [{"behavior": "...", "description": "..."}]
    behaviors: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # Work preferences: ["...", "..."]
    work_preferences: Mapped[list[str] | None] = mapped_column(FlexJSON)

    # Growth areas: [{"area": "...", "positive_frame": "..."}]
    growth_areas: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # Keywords that indicate strong fit: ["...", "..."]
    strong_fit_keywords: Mapped[list[str] | None] = mapped_column(FlexJSON)

    # Keywords that indicate friction: ["...", "..."]
    friction_keywords: Mapped[list[str] | None] = mapped_column(FlexJSON)

    # Management style preferences: {"works_with": ["..."], "doesnt_work": ["..."]}
    management_preferences: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)


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
    use_for: Mapped[list[str] | None] = mapped_column(FlexJSON)


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
    requirements: Mapped[list[str] | None] = mapped_column(FlexJSON)
    employment_type: Mapped[str | None] = mapped_column(String(50))  # full-time, part-time, ...

    # ── Language ─────────────────────────────────────────────
    language: Mapped[str | None] = mapped_column(String(10))  # en, da, ...

    # ── Lifecycle ─────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="new")  # new, ranked, applied, expired
    rank_score: Mapped[float | None] = mapped_column(default=None)
    rank_verdict: Mapped[str | None] = mapped_column(String(50))
    rank_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Raw scraper output (for debugging / re-parsing) ──────
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)


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
    portals_queried: Mapped[list[str]] = mapped_column(FlexJSON)  # ["linkedin", "jobindex", ...]
    jobs_found: Mapped[int] = mapped_column(default=0)
    jobs_new: Mapped[int] = mapped_column(default=0)  # after dedup
    jobs_expired: Mapped[int] = mapped_column(default=0)

    # ── Status ────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(30), default="running")  # running, completed, completed_with_errors, failed
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
    strengths: Mapped[list[str] | None] = mapped_column(FlexJSON)  # max 3
    gaps: Mapped[list[str] | None] = mapped_column(FlexJSON)  # max 3
    missing_keywords: Mapped[list[str] | None] = mapped_column(FlexJSON)  # max 5
    red_flags: Mapped[list[str] | None] = mapped_column(FlexJSON)  # max 3

    # ── Language ──────────────────────────────────────────────
    language: Mapped[str | None] = mapped_column(String(10))

    # ── Raw LLM response (for debugging) ──────────────────────
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)

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
    tailored_experience: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)
    # Missing keywords that were incorporated (and where)
    incorporated_keywords: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)
    # Red flags that were addressed
    addressed_red_flags: Mapped[list[str] | None] = mapped_column(FlexJSON)

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

    # ── Pipeline stage tracking ────────────────────────────────
    pipeline_stage: Mapped[str] = mapped_column(
        String(20), default="draft",
        comment="draft → reviewed → revised → compiled → verified",
    )
    # Draft LaTeX content (pre-review, for audit trail)
    draft_cv_tex: Mapped[str | None] = mapped_column(Text, comment="First draft CV LaTeX before review")
    draft_cover_letter_tex: Mapped[str | None] = mapped_column(Text, comment="First draft cover letter LaTeX before review")
    # Reviewer feedback (JSON)
    review_feedback: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)
    review_issues: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── ATS check (post-compilation quality gate) ───────────────
    ats_score: Mapped[float | None] = mapped_column(comment="ATS keyword coverage 0.0-1.0 (None = check not run)")
    ats_missing_keywords: Mapped[list[str] | None] = mapped_column(FlexJSON, comment="Job keywords not found in PDF text")
    ats_pass: Mapped[bool | None] = mapped_column(comment="Overall ATS compatibility verdict")
    ats_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="When ATS check was performed")

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
    interviewer_names: Mapped[list[str] | None] = mapped_column(FlexJSON)

    # ── Company research (interview-focused) ──────────────────
    company_research: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)
    # Verified conversation hooks: [{"topic": "...", "source_url": "..."}]
    conversation_hooks: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Likely questions ──────────────────────────────────────
    # [{"question": "...", "source": "feedback|gaps|requirements|stage", "priority": "high|medium|low"}]
    likely_questions: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── STAR answer mapping ───────────────────────────────────
    # [{"question": "...", "star_example_id": "...", "star_example_title": "..."}]
    star_mapping: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── New STAR drafts (for questions not covered by existing examples) ────
    # [{"question": "...", "draft_situation": "...", "draft_task": "...", "draft_action": "...", "draft_result": "..."}]
    new_star_drafts: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Consistency brief ─────────────────────────────────────
    # Claims from submitted CV/cover letter that interviewer will probe
    consistency_brief: Mapped[list[str] | None] = mapped_column(FlexJSON)

    # ── Tough questions (customized) ──────────────────────────
    # [{"question": "...", "answer": "..."}]
    tough_questions: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Questions to ask interviewer ──────────────────────────
    # [{"question": "...", "category": "role|team|tech|culture", "why_ask": "..."}]
    questions_to_ask: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Logistics ─────────────────────────────────────────────
    logistics: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)

    # ── Mock interview transcript (optional) ──────────────────
    mock_transcript: Mapped[str | None] = mapped_column(Text)

    # ── Raw LLM response (for debugging) ──────────────────────
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)

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
    valued_signals: Mapped[list[str] | None] = mapped_column(FlexJSON)

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
    experience_items: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Web-enriched competencies ────────────────────────────────
    # For each experience item, the competencies discovered via web search
    # [{"experience_item_id": "...", "competencies": ["...", "..."],
    #   "source": "direct_lookup|inferred", "source_urls": ["..."]}]
    enriched_competencies: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Proposed additions to profile ────────────────────────────
    # Competencies ready to be added to the candidate profile (pending user approval)
    # [{"category": "programming_ml|domain_expertise|software_tools",
    #   "skill": "...", "proficiency": "...", "evidence": "...", "source": "..."}]
    proposed_additions: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Status ───────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, completed, failed
    error_message: Mapped[str | None] = mapped_column(Text)

    # ── Raw LLM response (for debugging) ─────────────────────────
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)

    # ── Relationships ────────────────────────────────────────────
    candidate: Mapped["CandidateProfile"] = relationship(backref="competency_expansions")


# ═══════════════════════════════════════════════════════════════════
# USER SALARY DATA  (maps to /salary commands — per-user benchmarks)
# ═══════════════════════════════════════════════════════════════════


class UserSalaryData(Base, TimestampMixin):
    """Per-user salary benchmark data.

    Users can upload salary data (JSON or converted from Excel) so that
    the rank flow can show salary estimates alongside each job evaluation.

    The data is stored as a JSONB column matching the same schema as the
    global salary_data.json file, but scoped to a single user.

    If a user has uploaded their own data, it takes priority over the
    global file-based data.  If no data is available, the salary step
    is silently skipped during ranking.
    """

    __tablename__ = "user_salary_data"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # ── Source info ────────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(20), default="json_upload")
    # "json_upload" | "excel_converted"

    # ── Raw data ───────────────────────────────────────────────
    # The full companies array from salary_data.json format:
    # [{"company": "...", "city": "...", "categories": {...}}]
    companies: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Source metadata ────────────────────────────────────────
    # {"source": "union_statistics", "index_baseline": 100, "index_label": "Index", ...}
    # NOTE: named 'data_metadata' because 'metadata' is a SQLAlchemy reserved attribute.
    data_metadata: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)

    # ── Company count (denormalized for quick display) ─────────
    company_count: Mapped[int] = mapped_column(default=0)


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
    hard_skill_gaps: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Synthesized gaps (Pass 2) ─────────────────────────────────
    # [{"skill": "...", "type": "domain|soft|tooling|credential", "priority": "Critical|High|Medium|Low",
    #   "source": "LLM synthesis", "evidence": "..."}]
    synthesized_gaps: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Combined gap heatmap ──────────────────────────────────────
    # [{"skill": "...", "type": "hard|domain|soft|tooling|credential", "priority": "Critical|High|Medium|Low",
    #   "gap_source": "4/5 jobs, score 3.2 | LLM synthesis"}]
    gap_heatmap: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Learning plan ─────────────────────────────────────────────
    # [{"skill": "...", "type": "...", "priority": "...",
    #   "resources": [{"title": "...", "url": "...", "format": "course|video|article|certification",
    #                 "duration_hours": 10, "cost": "free|paid", "quality_score": 8}],
    #   "study_order": 1, "prerequisites": ["..."], "estimated_weeks": 4}]
    learning_plan: Mapped[list[dict[str, Any]] | None] = mapped_column(FlexJSON)

    # ── Status ────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, completed, failed
    error_message: Mapped[str | None] = mapped_column(Text)

    # ── Raw LLM response (for debugging) ──────────────────────────
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(FlexJSON)

    # ── Relationships ─────────────────────────────────────────────
    candidate: Mapped["CandidateProfile"] = relationship(backref="upskills")
    target_job_posting: Mapped["JobPosting | None"] = relationship()


# ═══════════════════════════════════════════════════════════════════
# ORCHESTRATOR  (execution queue, provider health, model health)
# ═══════════════════════════════════════════════════════════════════


class ExecutionJob(Base, TimestampMixin):
    """Persistent execution job record for the LLM orchestrator queue.

    Every LLM call goes through the orchestrator, which creates a row here
    tracking the entire lifecycle: from queued through to completed or failed.

    If the backend restarts, unfinished jobs can be resumed from their
    last checkpoint (never restart from zero).
    """

    __tablename__ = "execution_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # ── Job identity ──────────────────────────────────────────
    pipeline: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    group_id: Mapped[str | None] = mapped_column(String(36), index=True)
    description: Mapped[str | None] = mapped_column(String(500))

    # ── LLM task ──────────────────────────────────────────────
    messages: Mapped[dict | None] = mapped_column(FlexJSON)
    output_schema: Mapped[str | None] = mapped_column(String(100))

    # ── Status (state machine) ────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    # ── Provider / model assignment ───────────────────────────
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    attempt_tier: Mapped[int | None] = mapped_column(default=1)

    # ── Retry tracking ────────────────────────────────────────
    retry_count: Mapped[int] = mapped_column(default=0)
    max_retries: Mapped[int] = mapped_column(default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(50))

    # ── Timing ────────────────────────────────────────────────
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_time_ms: Mapped[int | None] = mapped_column()

    # ── Checkpoint data ───────────────────────────────────────
    checkpoint_data: Mapped[dict | None] = mapped_column(FlexJSON)
    result: Mapped[dict | None] = mapped_column(FlexJSON)

    # ── Worker info ───────────────────────────────────────────
    worker_id: Mapped[str | None] = mapped_column(String(50))


class ProviderHealth(Base, TimestampMixin):
    """Health metrics per LLM provider for a user.

    Tracks real-time status so the orchestrator can make intelligent
    failover decisions. One row per (user_id, provider).
    """

    __tablename__ = "provider_health"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # ── Priority tier (lower = higher priority, 1 = highest) ──
    priority: Mapped[int] = mapped_column(default=10)

    # ── Health state ──────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="healthy")
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Metrics ───────────────────────────────────────────────
    last_latency_ms: Mapped[int | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(50))
    total_calls: Mapped[int] = mapped_column(default=0)
    success_count: Mapped[int] = mapped_column(default=0)
    failure_count: Mapped[int] = mapped_column(default=0)
    rate_limit_count: Mapped[int] = mapped_column(default=0)
    timeout_count: Mapped[int] = mapped_column(default=0)
    consecutive_failures: Mapped[int] = mapped_column(default=0)

    # ── Health score (0.0 = dead, 1.0 = perfect) ─────────────
    health_score: Mapped[float] = mapped_column(default=1.0)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_provider_health_user_provider"),
    )


class ModelHealth(Base, TimestampMixin):
    """Health metrics per model within a provider.

    Models can be in various states: READY, BUSY, COOLDOWN, DISABLED.
    One row per (user_id, provider, model_name).
    """

    __tablename__ = "model_health"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # ── Priority within provider (lower = higher priority) ────
    priority: Mapped[int] = mapped_column(default=5)

    # ── Cost & capability ─────────────────────────────────────
    cost_rank: Mapped[int] = mapped_column(default=5)
    context_window: Mapped[int | None] = mapped_column(default=None)

    # ── Model state ───────────────────────────────────────────
    state: Mapped[str] = mapped_column(String(20), default="READY")
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Metrics ───────────────────────────────────────────────
    average_latency_ms: Mapped[float | None] = mapped_column()
    average_success_rate: Mapped[float] = mapped_column(default=1.0)
    total_calls: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "model_name",
            name="uq_model_health_user_provider_model",
        ),
    )


class ExecutionQueueState(Base):
    """Global queue state — persisted so the queue survives restarts.

    Singleton pattern: there should be only one row, keyed by user_id.
    """

    __tablename__ = "execution_queue_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # ── Queue control ─────────────────────────────────────────
    paused: Mapped[bool] = mapped_column(default=False)
    max_concurrency: Mapped[int] = mapped_column(default=4)
    active_workers: Mapped[int] = mapped_column(default=0)

    # ── Metrics ───────────────────────────────────────────────
    total_enqueued: Mapped[int] = mapped_column(default=0)
    total_completed: Mapped[int] = mapped_column(default=0)
    total_failed: Mapped[int] = mapped_column(default=0)
    total_cancelled: Mapped[int] = mapped_column(default=0)
