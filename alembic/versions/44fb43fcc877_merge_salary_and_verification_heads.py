"""merge_salary_and_verification_heads

Revision ID: 44fb43fcc877
Revises: d6fa2482e3bf, f1a2b3c4d5e6
Create Date: 2026-07-15 01:10:40.797073

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "44fb43fcc877"
down_revision: str | None = ("d6fa2482e3bf", "f1a2b3c4d5e6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
