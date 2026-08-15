"""add deleted_at to generated_cvs (artifact lifecycle, Fase 1a)

Adds nullable ``deleted_at`` so the storage sweeper can expire soft-deleted
rows after retention.  Backfills existing deleted rows so their retention clock
starts immediately (defaulting to an existing timestamp when present).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill: rows already marked deleted need a timestamp now, otherwise the
    # sweeper cannot know when their retention window starts.
    op.execute(
        """
        UPDATE generated_cvs
        SET deleted_at = COALESCE(updated_at, created_at)
        WHERE is_deleted = TRUE AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("generated_cvs", "deleted_at")