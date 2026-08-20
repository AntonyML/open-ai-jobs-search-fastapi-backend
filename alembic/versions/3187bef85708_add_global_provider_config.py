"""add_global_provider_config

Revision ID: 3187bef85708
Revises: c1d2e3f4a5b6
Create Date: 2026-08-11 22:47:46.515711

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.models import GLOBAL_PROVIDER_CONFIG_ID

# revision identifiers, used by Alembic.
revision: str = "3187bef85708"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Global admin-only provider config singleton (Fase 1).
    # Seed an empty row (provider NULL) so the panel always has a row to
    # read; the system falls back to .env until an admin configures it.
    op.create_table(
        "global_provider_config",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_base", sa.String(length=500), nullable=True),
        sa.Column("last_status", sa.String(length=20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO global_provider_config (id, provider) VALUES (:id, NULL) ON CONFLICT (id) DO NOTHING"
        ).bindparams(id=GLOBAL_PROVIDER_CONFIG_ID)
    )


def downgrade() -> None:
    op.drop_table("global_provider_config")
