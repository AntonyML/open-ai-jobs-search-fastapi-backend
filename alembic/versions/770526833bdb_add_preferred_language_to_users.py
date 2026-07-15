"""add_preferred_language_to_users

Revision ID: 770526833bdb
Revises: 113c1bad5c0a
Create Date: 2026-07-15 02:50:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "770526833bdb"
down_revision: Union[str, None] = "113c1bad5c0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("preferred_language", sa.String(length=10), nullable=False, server_default="en"),
    )


def downgrade() -> None:
    op.drop_column("users", "preferred_language")
