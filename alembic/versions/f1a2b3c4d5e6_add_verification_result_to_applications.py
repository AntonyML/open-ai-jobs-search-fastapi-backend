"""add_verification_result_to_applications

Revision ID: f1a2b3c4d5e6
Revises: ece27c8ea724
Create Date: 2026-07-14 20:00:00.000000

Adds the verification_result JSONB column to the applications table
for FASE 2 — Verification Checklist.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "ece27c8ea724"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add verification_result column as FlexJSON / JSONB."""
    op.add_column(
        "applications",
        sa.Column(
            "verification_result",
            JSONB().with_variant(JSONB(), "postgresql")
            .with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="FASE 2 — VerificationResult JSON from POST /apply/{id}/verify",
        ),
    )


def downgrade() -> None:
    """Remove verification_result column."""
    op.drop_column("applications", "verification_result")
