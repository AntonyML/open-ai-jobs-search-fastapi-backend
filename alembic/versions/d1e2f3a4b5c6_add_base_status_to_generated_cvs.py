"""add base_status to generated_cvs (max-2 base CV lifecycle)

Adds ``base_status`` (''active'' | ''obsolete'', NULL for personalized CVs) so
the system can keep at most one active base CV plus one recoverable previous
version. Backfills existing rows: the newest non-deleted base CV per user
becomes ''active'' and any older ones become ''obsolete''.

Revision ID: d1e2f3a4b5c6
Revises: a9b8c7d6e5f4
Create Date: 2026-08-13

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column("base_status", sa.String(length=20), nullable=True),
    )

    # Backfill: older non-deleted bases → 'obsolete'
    op.execute(
        """
        UPDATE generated_cvs
        SET base_status = 'obsolete'
        WHERE cv_type = 'base' AND is_deleted = false
        """
    )

    # Newest non-deleted base per user → 'active'
    op.execute(
        """
        UPDATE generated_cvs
        SET base_status = 'active'
        WHERE cv_type = 'base' AND is_deleted = false
          AND id IN (
            SELECT id FROM (
              SELECT id,
                     ROW_NUMBER() OVER (
                       PARTITION BY user_id
                       ORDER BY created_at DESC
                     ) AS rn
              FROM generated_cvs
              WHERE cv_type = 'base' AND is_deleted = false
            ) ranked
            WHERE rn = 1
          )
        """
    )


def downgrade() -> None:
    op.drop_column("generated_cvs", "base_status")
