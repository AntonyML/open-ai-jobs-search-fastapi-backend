"""Bootstrap initial plans; runtime never seeds or overwrites plans."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e7"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT 1 FROM plans LIMIT 1")).first() is not None:
        return
    table = sa.table(
        "plans",
        sa.column("id", sa.String),
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("price_monthly_usd", sa.Float),
        sa.column("price_yearly_usd", sa.Float),
        sa.column("credits_per_period", sa.Integer),
        sa.column("refill_cadence", sa.String),
        sa.column("refill_weekday", sa.Integer),
        sa.column("daily_quota", sa.Integer),
        sa.column("weekly_quota", sa.Integer),
        sa.column("features", sa.JSON),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": "bootstrap-free",
                "key": "free",
                "name": "Free",
                "price_monthly_usd": 0.0,
                "price_yearly_usd": 0.0,
                "credits_per_period": 2,
                "refill_cadence": "weekly",
                "refill_weekday": 0,
                "daily_quota": 0,
                "weekly_quota": 0,
                "features": ["cv_base", "cv_adapted"],
                "is_active": True,
                "sort_order": 10,
            },
            {
                "id": "bootstrap-pro",
                "key": "pro",
                "name": "Pro",
                "price_monthly_usd": 24.99,
                "price_yearly_usd": 249.0,
                "credits_per_period": 80,
                "refill_cadence": "period",
                "refill_weekday": 0,
                "daily_quota": 0,
                "weekly_quota": 0,
                "features": ["cv_base", "cv_adapted"],
                "is_active": True,
                "sort_order": 20,
            },
            {
                "id": "bootstrap-max",
                "key": "max",
                "name": "Max",
                "price_monthly_usd": 69.99,
                "price_yearly_usd": 699.0,
                "credits_per_period": 350,
                "refill_cadence": "period",
                "refill_weekday": 0,
                "daily_quota": 12,
                "weekly_quota": 50,
                "features": ["cv_base", "cv_adapted", "pipeline", "expand", "upskill"],
                "is_active": True,
                "sort_order": 30,
            },
        ],
    )


def downgrade() -> None:
    pass
