"""add_plan_usage_caps

Revision ID: b7c8d9e0f1a2
Revises: c2d3e4f5a6b7
Create Date: 2026-08-20 00:00:00.000000

Adds per-plan usage caps to the plans table (migrates the hardcoded
tier limits that lived in app/services/tiers.py into the DB catalog).

Values mirror the legacy tiers:
- free: max_apply_count=5, max_prepare_count=5, max_rank_iterations=3, max_track_count=5
- pro/max (and any new plan): 1000 / 1000 / 100 / 1000 via column defaults
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("max_apply_count", sa.Integer(), server_default="1000", nullable=False))
    op.add_column("plans", sa.Column("max_prepare_count", sa.Integer(), server_default="1000", nullable=False))
    op.add_column("plans", sa.Column("max_rank_iterations", sa.Integer(), server_default="100", nullable=False))
    op.add_column("plans", sa.Column("max_track_count", sa.Integer(), server_default="1000", nullable=False))
    op.add_column("plans", sa.Column("expand_locked", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("plans", sa.Column("upskill_locked", sa.Boolean(), server_default=sa.text("false"), nullable=False))

    op.execute(
        sa.text(
            "UPDATE plans SET max_apply_count = 5, max_prepare_count = 5, "
            "max_rank_iterations = 3, max_track_count = 5 WHERE key = 'free'"
        )
    )


def downgrade() -> None:
    op.drop_column("plans", "upskill_locked")
    op.drop_column("plans", "expand_locked")
    op.drop_column("plans", "max_track_count")
    op.drop_column("plans", "max_rank_iterations")
    op.drop_column("plans", "max_prepare_count")
    op.drop_column("plans", "max_apply_count")
