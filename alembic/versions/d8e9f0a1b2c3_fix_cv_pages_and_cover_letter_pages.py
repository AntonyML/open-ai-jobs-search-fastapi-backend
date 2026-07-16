"""Fix cv_pages and cover_letter_pages columns on applications.

These columns were defined as ``mapped_column`` (the function itself) instead of
``mapped_column()`` (calling it), so they were never created in the database.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-07-16 01:00:00.000000
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: ClassVar[set[str] | None] = None
depends_on: ClassVar[set[str] | None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("cv_pages", sa.Integer(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("cover_letter_pages", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "cover_letter_pages")
    op.drop_column("applications", "cv_pages")
