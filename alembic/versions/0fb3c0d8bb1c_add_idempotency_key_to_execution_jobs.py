"""add_idempotency_key_to_execution_jobs

Revision ID: 0fb3c0d8bb1c
Revises: ff90d6695e6a
Create Date: 2026-07-22 22:15:54.107709

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0fb3c0d8bb1c"
down_revision: str | None = "ff90d6695e6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # idempotency_key for POST /rank/ idempotency (Fase 6)
    op.add_column("execution_jobs", sa.Column("idempotency_key", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_execution_jobs_idempotency_key"), "execution_jobs", ["idempotency_key"], unique=True)

    # Fase 4 dimension columns (not yet applied from prior migration)
    op.add_column(
        "rank_evaluations",
        sa.Column(
            "technical_fit",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "rank_evaluations",
        sa.Column(
            "relevant_experience",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "rank_evaluations",
        sa.Column(
            "constraints_fit",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "rank_evaluations",
        sa.Column(
            "career_alignment",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "rank_evaluations",
        sa.Column(
            "behavioral_fit",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("execution_jobs", "idempotency_key")
    op.drop_index(op.f("ix_execution_jobs_idempotency_key"), table_name="execution_jobs")
    op.drop_column("rank_evaluations", "technical_fit")
    op.drop_column("rank_evaluations", "relevant_experience")
    op.drop_column("rank_evaluations", "constraints_fit")
    op.drop_column("rank_evaluations", "career_alignment")
    op.drop_column("rank_evaluations", "behavioral_fit")
