"""Add role (admin/client) and tier (free/premium) columns to users.

Revision ID: c7d8e9f0a1b2
Revises: 44fb43fcc877
Create Date: 2026-07-16 00:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "9a8b7c6d5e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(20), server_default="client", nullable=False))
    op.add_column("users", sa.Column("tier", sa.String(20), server_default="free", nullable=False))


def downgrade() -> None:
    op.drop_column("users", "tier")
    op.drop_column("users", "role")
