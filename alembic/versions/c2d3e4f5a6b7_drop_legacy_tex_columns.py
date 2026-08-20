"""drop_legacy_tex_columns

Revision ID: c2d3e4f5a6b7
Revises: f3a4b5c6d7e8
Create Date: 2026-08-20 00:00:00.000000

Removes the legacy LaTeX-era columns from the applications table:
- cv_tex_path / cover_letter_tex_path (never written by the Typst pipeline)
- draft_cv_tex / draft_cover_letter_tex (misnamed draft storage; the final
  structured CV now lives in cv_json)

Data in these columns is intentionally discarded (test environment).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the four legacy columns."""
    op.drop_column("applications", "draft_cover_letter_tex")
    op.drop_column("applications", "draft_cv_tex")
    op.drop_column("applications", "cover_letter_tex_path")
    op.drop_column("applications", "cv_tex_path")


def downgrade() -> None:
    """Re-add the legacy columns (data is lost)."""
    import sqlalchemy as sa

    op.add_column("applications", sa.Column("cv_tex_path", sa.String(length=500), nullable=True))
    op.add_column("applications", sa.Column("cover_letter_tex_path", sa.String(length=500), nullable=True))
    op.add_column("applications", sa.Column("draft_cv_tex", sa.Text(), nullable=True))
    op.add_column("applications", sa.Column("draft_cover_letter_tex", sa.Text(), nullable=True))
