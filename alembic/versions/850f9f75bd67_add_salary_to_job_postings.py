"""add_salary_to_job_postings

Revision ID: 850f9f75bd67
Revises: 0fb3c0d8bb1c
Create Date: 2026-07-27 20:08:12.549600

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "850f9f75bd67"
down_revision: str | None = "0fb3c0d8bb1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_postings", sa.Column("salary", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("job_postings", "salary")
