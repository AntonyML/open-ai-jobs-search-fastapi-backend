"""add portfolio_url and certifications to candidate_profiles

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-21 00:00:00.000000

Adds portfolio_url (VARCHAR 500) and certifications (JSONB) to candidate_profiles
to ensure full persistence of candidate credentials and personal websites.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Cross-engine JSON type
FlexJSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("candidate_profiles", sa.Column("portfolio_url", sa.String(length=500), nullable=True))
    op.add_column("candidate_profiles", sa.Column("certifications", FlexJSON, nullable=True))


def downgrade() -> None:
    op.drop_column("candidate_profiles", "certifications")
    op.drop_column("candidate_profiles", "portfolio_url")
