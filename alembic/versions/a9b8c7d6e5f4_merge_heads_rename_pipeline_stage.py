"""merge divergent heads and rename applications.pipeline_stage to stage

- The migration graph had two heads (b1c2d3e4f5a6, f7e8d9c0b1a2); this
  migration merges them into a single head so `alembic upgrade head`
  resolves unambiguously.
- Renames the internal CV-compilation stage column on `applications`
  (queued → … → verified) from `pipeline_stage` to `stage`, matching the
  feature-based naming of the rest of the system.

Revision ID: a9b8c7d6e5f4
Revises: b1c2d3e4f5a6, f7e8d9c0b1a2
Create Date: 2026-08-13

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: str | Sequence[str] | None = ("b1c2d3e4f5a6", "f7e8d9c0b1a2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("applications", "pipeline_stage", new_column_name="stage")


def downgrade() -> None:
    op.alter_column("applications", "stage", new_column_name="pipeline_stage")
