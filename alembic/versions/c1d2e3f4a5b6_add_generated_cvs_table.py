"""add generated_cvs table (CV generator FASE 1)

Revision ID: c1d2e3f4a5b6
Revises: 850f9f75bd67
Create Date: 2026-08-11 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "850f9f75bd67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FlexJSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "generated_cvs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("cv_type", sa.String(length=20), nullable=False, server_default="base"),
        sa.Column("job_url", sa.String(length=500), nullable=True),
        sa.Column("job_description_text", sa.Text(), nullable=True),
        sa.Column("cv_json", FlexJSON, nullable=False),
        sa.Column("pdf_path", sa.String(length=1000), nullable=True),
        sa.Column("analysis", FlexJSON, nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_generated_cvs_user_id"), "generated_cvs", ["user_id"], unique=False)
    op.create_index(op.f("ix_generated_cvs_cv_type"), "generated_cvs", ["cv_type"], unique=False)
    op.create_index(op.f("ix_generated_cvs_created_at"), "generated_cvs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_generated_cvs_created_at"), table_name="generated_cvs")
    op.drop_index(op.f("ix_generated_cvs_cv_type"), table_name="generated_cvs")
    op.drop_index(op.f("ix_generated_cvs_user_id"), table_name="generated_cvs")
    op.drop_table("generated_cvs")
