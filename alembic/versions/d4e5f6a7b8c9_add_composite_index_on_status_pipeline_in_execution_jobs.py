"""add composite index on (status, pipeline) in execution_jobs, drop redundant single-column status index

The orchestrator's hot path queries filter by both columns together
(e.g. "find next pending job for pipeline X"). A composite index is
much faster than using separate indexes.

The individual pipeline index is kept for queries that filter by
pipeline alone (without status).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-16 12:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_execution_jobs_status", table_name="execution_jobs")
    op.create_index(
        "ix_execution_jobs_status_pipeline",
        "execution_jobs",
        ["status", "pipeline"],
    )


def downgrade() -> None:
    op.drop_index("ix_execution_jobs_status_pipeline", table_name="execution_jobs")
    op.create_index("ix_execution_jobs_status", "execution_jobs", ["status"])
