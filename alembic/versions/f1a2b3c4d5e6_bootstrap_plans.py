"""Bootstrap initial plans; runtime never seeds or overwrites plans."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT 1 FROM plans LIMIT 1")).first() is not None:
        return
    plans = [
        ("free", "Free", 0.0, 0.0, 2, "weekly", 0, 0, '["cv_base", "cv_adapted"]', 10),
        ("pro", "Pro", 24.99, 249.0, 80, "period", 0, 0, '["cv_base", "cv_adapted"]', 20),
        ("max", "Max", 69.99, 699.0, 350, "period", 12, 50, '["cv_base", "cv_adapted", "pipeline", "expand", "upskill"]', 30),
    ]
    table = sa.table(
        "plans",
        sa.column("id", sa.String), sa.column("key", sa.String), sa.column("name", sa.String),
        sa.column("price_monthly_usd", sa.Float), sa.column("price_yearly_usd", sa.Float),
        sa.column("credits_per_period", sa.Integer), sa.column("refill_cadence", sa.String),
        sa.column("refill_weekday", sa.Integer), sa.column("daily_quota", sa.Integer),
        sa.column("weekly_quota", sa.Integer), sa.column("features", sa.JSON),
        sa.column("is_active", sa.Boolean), sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(table, [
        {"id": f"bootstrap-{key}", "key": key, "name": name, "price_monthly_usd": monthly,
         "price_yearly_usd": yearly, "credits_per_period": credits, "refill_cadence": cadence,
         "refill_weekday": 0, "daily_quota": daily, "weekly_quota": weekly, "features": features,
         "is_active": True, "sort_order": sort_order}
        for key, name, monthly, yearly, credits, cadence, daily, weekly, features, sort_order in plans
    ])


def downgrade() -> None:
    # Never delete plans on downgrade: subscriptions may reference them.
    pass
