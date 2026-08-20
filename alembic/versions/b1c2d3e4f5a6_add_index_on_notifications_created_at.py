"""add index on app_notifications.created_at (TTL purge)

Revision ID: b1c2d3e4f5a6
Revises: a2b3c4d5e6f7
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_app_notifications_created_at",
        "app_notifications",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_app_notifications_created_at", table_name="app_notifications")
