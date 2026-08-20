"""add job_target to candidate_profiles

Revision ID: 5e788b9bfd16
Revises: d4e5f6a7b8c9
Create Date: 2026-07-21 17:14:45.240055

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e788b9bfd16"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "job_target", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "job_target")
