"""add_applications_cv_json

Revision ID: f3a4b5c6d7e8
Revises: f1a2b3c4d5e7
Create Date: 2026-08-20 00:00:00.000000

Adds the cv_json FlexJSON column to the applications table — the final
structured CV JSON (post-revision) used as source of truth for verification.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "f1a2b3c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add cv_json column as FlexJSON / JSONB."""
    op.add_column(
        "applications",
        sa.Column(
            "cv_json",
            JSONB().with_variant(JSONB(), "postgresql")
            .with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="Final structured CV JSON (post-revision) — source of truth for verification",
        ),
    )


def downgrade() -> None:
    """Remove cv_json column."""
    op.drop_column("applications", "cv_json")