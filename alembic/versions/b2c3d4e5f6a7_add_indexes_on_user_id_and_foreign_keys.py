"""add indexes on all user_id (and other high-traffic FK columns)

Adds B-tree indexes on foreign-key columns that are used in WHERE
clauses throughout the application but were missing explicit indexes.

The biggest gain comes from job_postings.user_id, applications.user_id,
and rank_evaluations.user_id — these are scanned on every user request.

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── user_id indexes (every FK to users.id) ────────────────
    op.create_index("ix_provider_credentials_user_id", "provider_credentials", ["user_id"])
    op.create_index("ix_job_postings_user_id", "job_postings", ["user_id"])
    op.create_index("ix_scrape_runs_user_id", "scrape_runs", ["user_id"])
    op.create_index("ix_rank_evaluations_user_id", "rank_evaluations", ["user_id"])
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_interview_preps_user_id", "interview_preps", ["user_id"])
    op.create_index("ix_outcomes_user_id", "outcomes", ["user_id"])
    op.create_index("ix_competency_expansions_user_id", "competency_expansions", ["user_id"])
    op.create_index("ix_upskills_user_id", "upskills", ["user_id"])
    op.create_index("ix_execution_jobs_user_id", "execution_jobs", ["user_id"])

    # ── candidate_id indexes ──────────────────────────────────
    op.create_index("ix_star_examples_candidate_id", "star_examples", ["candidate_id"])
    op.create_index("ix_competency_expansions_candidate_id", "competency_expansions", ["candidate_id"])
    op.create_index("ix_upskills_candidate_id", "upskills", ["candidate_id"])

    # ── job_posting_id indexes ────────────────────────────────
    op.create_index("ix_applications_job_posting_id", "applications", ["job_posting_id"])

    # ── application_id indexes ────────────────────────────────
    op.create_index("ix_interview_preps_application_id", "interview_preps", ["application_id"])
    op.create_index("ix_outcomes_application_id", "outcomes", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_provider_credentials_user_id", table_name="provider_credentials")
    op.drop_index("ix_job_postings_user_id", table_name="job_postings")
    op.drop_index("ix_scrape_runs_user_id", table_name="scrape_runs")
    op.drop_index("ix_rank_evaluations_user_id", table_name="rank_evaluations")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_index("ix_interview_preps_user_id", table_name="interview_preps")
    op.drop_index("ix_outcomes_user_id", table_name="outcomes")
    op.drop_index("ix_competency_expansions_user_id", table_name="competency_expansions")
    op.drop_index("ix_upskills_user_id", table_name="upskills")
    op.drop_index("ix_execution_jobs_user_id", table_name="execution_jobs")
    op.drop_index("ix_star_examples_candidate_id", table_name="star_examples")
    op.drop_index("ix_competency_expansions_candidate_id", table_name="competency_expansions")
    op.drop_index("ix_upskills_candidate_id", table_name="upskills")
    op.drop_index("ix_applications_job_posting_id", table_name="applications")
    op.drop_index("ix_interview_preps_application_id", table_name="interview_preps")
    op.drop_index("ix_outcomes_application_id", table_name="outcomes")
