"""add payload column to app_notifications (deep-link purchase requests)

Revision ID: a2b3c4d5e6f7
Revises: e5f6a7b8c9d0
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

jsonb = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "app_notifications",
        sa.Column("payload", jsonb, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("app_notifications", "payload")
