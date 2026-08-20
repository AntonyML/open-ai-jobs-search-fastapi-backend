"""credit_cost_config: typed table replaces the app_config JSON blob

Revision ID: b6c7d8e9f0a1
Revises: a4b5c6d7e8f9
Create Date: 2026-08-14

Plan.md §8.2 — single source of truth for credit costs.  Sequence:
1. create ``credit_cost_config`` (PK action, CHECK cost >= 0, audit + version)
2. backfill idempotently from ``app_config['credit_costs']`` (ON CONFLICT
   DO NOTHING), seeding catalog defaults for missing keys
3. verify every row matches the backfill values (raise -> full rollback)
4. only after verification passes: delete the legacy JSON row

downgrade restores the JSON blob from the table so no data is lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.services.credit_costs import compute_backfill

# revision identifiers, used by Alembic.
revision: str = "b6c7d8e9f0a1"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_KEY = "credit_costs"


def upgrade() -> None:
    op.create_table(
        "credit_cost_config",
        sa.Column("action", sa.String(50), primary_key=True),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("cost >= 0", name="ck_credit_cost_config_cost_nonneg"),
    )

    conn = op.get_bind()

    # 1. Read the legacy JSON blob (may not exist on fresh installs).
    legacy = {}
    row = conn.execute(sa.text("SELECT value FROM app_config WHERE key = :k"), {"k": LEGACY_KEY}).first()
    if row is not None and isinstance(row[0], dict):
        legacy = row[0]

    # 2. Backfill — idempotent (ON CONFLICT DO NOTHING); seed catalog
    #    defaults for actions missing from the JSON.
    backfill = compute_backfill(legacy)
    for key, cost in backfill.items():
        conn.execute(
            sa.text(
                "INSERT INTO credit_cost_config (action, cost, version) "
                "VALUES (:a, :c, 1) ON CONFLICT (action) DO NOTHING"
            ),
            {"a": key, "c": cost},
        )

    # 3. Verify every row matches the backfill values — raise (full rollback)
    #    on any mismatch.
    rows = conn.execute(sa.text("SELECT action, cost FROM credit_cost_config")).fetchall()
    got = {r[0]: r[1] for r in rows}
    for key, expected in backfill.items():
        if got.get(key) != expected:
            raise RuntimeError(
                f"credit_cost_config backfill verification failed for "
                f"'{key}': row={got.get(key)!r} expected={expected!r} "
                f"— rolling back"
            )

    # 4. Single source of truth — drop the legacy JSON blob.
    conn.execute(sa.text("DELETE FROM app_config WHERE key = :k"), {"k": LEGACY_KEY})


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT action, cost FROM credit_cost_config")).fetchall()
    if rows:
        import json

        payload = {r[0]: r[1] for r in rows}
        conn.execute(
            sa.text(
                "INSERT INTO app_config (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": LEGACY_KEY, "v": json.dumps(payload)},
        )
    op.drop_table("credit_cost_config")
