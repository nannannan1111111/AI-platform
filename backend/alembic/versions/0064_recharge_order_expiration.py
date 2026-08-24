"""Expire and cancel pending recharge orders."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0064_recharge_order_expiration"
down_revision: str | None = "0063_generated_media_thumbnails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the one-minute checkout deadline and cancellation audit fields."""
    with op.batch_alter_table("recharge_orders") as batch:
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cancellation_reason", sa.String(length=32), nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        op.execute(sa.text("UPDATE recharge_orders SET expires_at = datetime(created_at, '+1 minute')"))
    else:
        op.execute(sa.text("UPDATE recharge_orders SET expires_at = created_at + INTERVAL '1 minute'"))
    with op.batch_alter_table("recharge_orders") as batch:
        batch.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.drop_constraint("ck_recharge_order_status", type_="check")
        batch.create_check_constraint(
            "ck_recharge_order_status",
            "status IN ('pending', 'cancelled', 'expired', 'paid', 'charged_back')",
        )
        batch.create_check_constraint(
            "ck_recharge_order_cancellation_reason",
            "cancellation_reason IS NULL OR cancellation_reason IN ('user_cancelled', 'expired')",
        )


def downgrade() -> None:
    """Restore pending status before removing cancellation metadata."""
    op.execute(
        sa.text(
            "UPDATE recharge_orders SET status = 'pending', updated_at = created_at "
            "WHERE status IN ('cancelled', 'expired')"
        )
    )
    with op.batch_alter_table("recharge_orders") as batch:
        batch.drop_constraint("ck_recharge_order_cancellation_reason", type_="check")
        batch.drop_constraint("ck_recharge_order_status", type_="check")
        batch.create_check_constraint(
            "ck_recharge_order_status",
            "status IN ('pending', 'paid', 'charged_back')",
        )
        batch.drop_column("cancellation_reason")
        batch.drop_column("cancelled_at")
        batch.drop_column("expires_at")
