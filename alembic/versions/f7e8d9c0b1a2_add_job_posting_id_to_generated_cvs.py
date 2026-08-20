"""add job_posting_id to generated_cvs

Links personalized (adapted) CVs to the job posting they were adapted from,
enabling the Perfil → CV base → CV adaptado relationship.

Revision ID: f7e8d9c0b1a2
Revises: 3187bef85708
Create Date: 2026-08-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7e8d9c0b1a2"
down_revision: str | None = "3187bef85708"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column("job_posting_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f("ix_generated_cvs_job_posting_id"),
        "generated_cvs",
        ["job_posting_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_generated_cvs_job_posting_id"), table_name="generated_cvs")
    op.drop_column("generated_cvs", "job_posting_id")
