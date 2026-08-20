"""add unique constraint on (portal, external_id) in job_postings

First removes any duplicate rows keeping the oldest one (by created_at),
then adds the unique constraint to enforce deduplication at DB level.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-16 12:05:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove duplicates: keep the row with the lowest ctid for each (portal, external_id)
    op.execute("""
        DELETE FROM job_postings
        WHERE ctid NOT IN (
            SELECT min(ctid)
            FROM job_postings
            GROUP BY portal, external_id
        )
    """)
    op.create_unique_constraint(
        "uq_job_postings_portal_external_id",
        "job_postings",
        ["portal", "external_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_job_postings_portal_external_id",
        "job_postings",
        type_="unique",
    )
