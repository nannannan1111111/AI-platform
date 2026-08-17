"""Persist versioned recharge orders and verified payment events."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_recharge_orders"
down_revision: str | None = "0006_generation_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create recharge order snapshots and idempotent payment success events."""
    op.create_table(
        "recharge_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("package_version_id", sa.String(length=36), nullable=False),
        sa.Column("package_code", sa.String(length=64), nullable=False),
        sa.Column("payment_cny_units", sa.BigInteger(), nullable=False),
        sa.Column("credit_units", sa.BigInteger(), nullable=False),
        sa.Column("payment_provider", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_reference", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("payment_cny_units > 0", name="ck_recharge_order_payment_positive"),
        sa.CheckConstraint("credit_units > 0", name="ck_recharge_order_credits_positive"),
        sa.CheckConstraint("status IN ('pending', 'paid')", name="ck_recharge_order_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["recharge_package_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_space_id", "idempotency_key", name="uq_recharge_order_idempotency"),
    )
    op.create_table(
        "payment_success_events",
        sa.Column("payment_provider", sa.String(length=64), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("paid_payment_cny_units", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("paid_payment_cny_units > 0", name="ck_payment_success_amount_positive"),
        sa.ForeignKeyConstraint(["order_id"], ["recharge_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("payment_provider", "provider_event_id"),
        sa.UniqueConstraint("order_id", name="uq_payment_success_order"),
    )


def downgrade() -> None:
    """Remove payment success events and recharge order snapshots."""
    op.drop_table("payment_success_events")
    op.drop_table("recharge_orders")
