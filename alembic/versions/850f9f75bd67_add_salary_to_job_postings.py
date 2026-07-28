"""add_salary_to_job_postings

Revision ID: 850f9f75bd67
Revises: 0fb3c0d8bb1c
Create Date: 2026-07-27 20:08:12.549600

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '850f9f75bd67'
down_revision: Union[str, None] = '0fb3c0d8bb1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_postings', sa.Column('salary', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('job_postings', 'salary')
