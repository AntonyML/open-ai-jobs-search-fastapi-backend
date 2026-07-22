"""remove duplicated full_name and email from candidate_profiles

full_name and email were duplicated from users table — single source
of truth is now User. CandidateProfile reads these via the .user relationship.

Revision ID: e1f2a3b4c5d6
Revises: 5e788b9bfd16
Create Date: 2026-07-21 21:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "5e788b9bfd16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("candidate_profiles", "full_name")
    op.drop_column("candidate_profiles", "email")


def downgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column("full_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column("email", sa.String(length=255), nullable=True),
    )
