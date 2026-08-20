"""add billing tables (plans, subscriptions, credits, notifications)

Revision ID: e5f6a7b8c9d0
Revises: 850f9f75bd67
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "850f9f75bd67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

jsonb = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # ── plans ──
    op.create_table(
        "plans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_monthly_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price_yearly_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("credits_per_period", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refill_cadence", sa.String(length=20), nullable=False, server_default="period"),
        sa.Column("refill_weekday", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("daily_quota", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weekly_quota", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("features", jsonb, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_plans_key", "plans", ["key"], unique=True)

    # ── user_subscriptions ──
    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_key", sa.String(length=50), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="purchase"),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("price_paid", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_user_subscriptions_user_id", "user_subscriptions", ["user_id"])
    op.create_index("ix_user_subscriptions_plan_key", "user_subscriptions", ["plan_key"])
    op.create_index("ix_user_subscriptions_correlation_id", "user_subscriptions", ["correlation_id"], unique=True)

    # ── credit_accounts ──
    op.create_table(
        "credit_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "subscription_id",
            sa.String(length=36),
            sa.ForeignKey("user_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_earned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_refill_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quota_day_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quota_day_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quota_week_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quota_week_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_credit_accounts_user_id", "credit_accounts", ["user_id"], unique=True)

    # ── credit_transactions ──
    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "subscription_id",
            sa.String(length=36),
            sa.ForeignKey("user_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("correlation_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("credits_delta", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(length=100), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_credit_transactions_user_id", "credit_transactions", ["user_id"])
    op.create_index("ix_credit_transactions_action", "credit_transactions", ["action"])
    op.create_index("ix_credit_transactions_correlation_id", "credit_transactions", ["correlation_id"])

    # ── app_config (admin-editable key/value) ──
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(length=50), primary_key=True),
        sa.Column("value", jsonb, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── app_notifications ──
    op.create_table(
        "app_notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False, server_default="info"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_app_notifications_user_id", "app_notifications", ["user_id"])


def downgrade() -> None:
    op.drop_table("app_notifications")
    op.drop_table("app_config")
    op.drop_table("credit_transactions")
    op.drop_table("credit_accounts")
    op.drop_table("user_subscriptions")
    op.drop_table("plans")
