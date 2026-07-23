"""recreate all indexes that were incorrectly dropped, plus new composite indexes

Migration 5e788b9bfd16 dropped all FK indexes (detected by --autogenerate as
"redundant"). This restores them and adds composite (user_id, created_at DESC)
indexes for the three most-fetched tables.

Uses IF NOT EXISTS so it is idempotent whether or not 5e788b9bfd16 has been
fixed (Acción B). Fresh installs + fixed 5e788b9bfd16 = indexes already exist
→ no-op. Existing DBs where indexes were dropped → they are recreated.

Revision ID: f6a7b8c9d0e1
Revises: 79905e25e8c5
Create Date: 2026-07-22 01:15:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "79905e25e8c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IDX = "CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})"
DROP = "DROP INDEX IF EXISTS {name}"

# fmt: off
_IDX_SPEC: list[tuple[str, str, str]] = [
    # user_id FK indexes
    ("ix_provider_credentials_user_id",     "provider_credentials",       "user_id"),
    ("ix_job_postings_user_id",             "job_postings",              "user_id"),
    ("ix_scrape_runs_user_id",              "scrape_runs",               "user_id"),
    ("ix_rank_evaluations_user_id",         "rank_evaluations",          "user_id"),
    ("ix_applications_user_id",             "applications",              "user_id"),
    ("ix_interview_preps_user_id",          "interview_preps",           "user_id"),
    ("ix_outcomes_user_id",                 "outcomes",                  "user_id"),
    ("ix_competency_expansions_user_id",    "competency_expansions",     "user_id"),
    ("ix_upskills_user_id",                 "upskills",                  "user_id"),
    ("ix_execution_jobs_user_id",           "execution_jobs",            "user_id"),
    # candidate_id FK indexes
    ("ix_star_examples_candidate_id",       "star_examples",             "candidate_id"),
    ("ix_competency_expansions_candidate_id","competency_expansions",    "candidate_id"),
    ("ix_upskills_candidate_id",            "upskills",                  "candidate_id"),
    # job_posting_id FK indexes
    ("ix_applications_job_posting_id",      "applications",              "job_posting_id"),
    # application_id FK indexes
    ("ix_interview_preps_application_id",   "interview_preps",           "application_id"),
    ("ix_outcomes_application_id",          "outcomes",                  "application_id"),
    # updated_at (ORDER BY recency)
    ("ix_job_postings_updated_at",          "job_postings",              "updated_at"),
    # Composite (user_id, created_at DESC) for dashboard queries
    ("ix_job_postings_user_id_created",     "job_postings",              "user_id, created_at DESC"),
    ("ix_applications_user_id_created",     "applications",              "user_id, created_at DESC"),
    ("ix_outcomes_user_id_created",         "outcomes",                  "user_id, created_at DESC"),
]


def upgrade() -> None:
    for name, table, cols in _IDX_SPEC:
        op.execute(IDX.format(name=name, table=table, cols=cols))


def downgrade() -> None:
    for name, table, cols in reversed(_IDX_SPEC):
        op.execute(DROP.format(name=name))


def downgrade() -> None:
    op.drop_index("ix_outcomes_user_id_created", table_name="outcomes")
    op.drop_index("ix_applications_user_id_created", table_name="applications")
    op.drop_index("ix_job_postings_user_id_created", table_name="job_postings")
    op.drop_index("ix_job_postings_updated_at", table_name="job_postings")
    op.drop_index("ix_outcomes_application_id", table_name="outcomes")
    op.drop_index("ix_interview_preps_application_id", table_name="interview_preps")
    op.drop_index("ix_applications_job_posting_id", table_name="applications")
    op.drop_index("ix_upskills_candidate_id", table_name="upskills")
    op.drop_index("ix_competency_expansions_candidate_id", table_name="competency_expansions")
    op.drop_index("ix_star_examples_candidate_id", table_name="star_examples")
    op.drop_index("ix_execution_jobs_user_id", table_name="execution_jobs")
    op.drop_index("ix_upskills_user_id", table_name="upskills")
    op.drop_index("ix_competency_expansions_user_id", table_name="competency_expansions")
    op.drop_index("ix_outcomes_user_id", table_name="outcomes")
    op.drop_index("ix_interview_preps_user_id", table_name="interview_preps")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_index("ix_rank_evaluations_user_id", table_name="rank_evaluations")
    op.drop_index("ix_scrape_runs_user_id", table_name="scrape_runs")
    op.drop_index("ix_job_postings_user_id", table_name="job_postings")
    op.drop_index("ix_provider_credentials_user_id", table_name="provider_credentials")
