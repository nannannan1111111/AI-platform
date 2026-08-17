"""Persist recharge posting links and verified chargeback events."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_recharge_order_chargebacks"
down_revision: str | None = "0007_recharge_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Link paid orders to their recharge posting and add chargeback facts."""
    with op.batch_alter_table("recharge_orders") as batch:
        batch.add_column(sa.Column("recharge_posting_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("charged_back_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("chargeback_reference", sa.String(length=255), nullable=True))
    op.execute(
        sa.text(
            "UPDATE recharge_orders "
            "SET recharge_posting_id = ("
            "SELECT credit_postings.id FROM credit_postings "
            "WHERE credit_postings.reference = recharge_orders.payment_reference"
            ") WHERE status = 'paid'"
        )
    )
    with op.batch_alter_table("recharge_orders") as batch:
        batch.create_foreign_key(
            "fk_recharge_order_recharge_posting",
            "credit_postings",
            ["recharge_posting_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.drop_constraint("ck_recharge_order_status", type_="check")
        batch.create_check_constraint(
            "ck_recharge_order_status",
            "status IN ('pending', 'paid', 'charged_back')",
        )
    op.create_table(
        "payment_chargeback_events",
        sa.Column("payment_provider", sa.String(length=64), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("charged_back_payment_cny_units", sa.BigInteger(), nullable=False),
        sa.Column("reversal_posting_id", sa.String(length=36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("charged_back_payment_cny_units > 0", name="ck_chargeback_amount_positive"),
        sa.ForeignKeyConstraint(["order_id"], ["recharge_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reversal_posting_id"], ["credit_postings.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("payment_provider", "provider_event_id"),
        sa.UniqueConstraint("order_id", name="uq_chargeback_order"),
        sa.UniqueConstraint("reversal_posting_id", name="uq_chargeback_reversal_posting"),
    )


def downgrade() -> None:
    """Remove verified chargeback events and recharge posting links."""
    op.drop_table("payment_chargeback_events")
    op.execute(
        sa.text(
            "UPDATE recharge_orders SET status = 'paid', "
            "updated_at = COALESCE(charged_back_at, updated_at) "
            "WHERE status = 'charged_back'"
        )
    )
    with op.batch_alter_table("recharge_orders") as batch:
        batch.drop_constraint("ck_recharge_order_status", type_="check")
        batch.create_check_constraint("ck_recharge_order_status", "status IN ('pending', 'paid')")
        batch.drop_constraint("fk_recharge_order_recharge_posting", type_="foreignkey")
        batch.drop_column("chargeback_reference")
        batch.drop_column("charged_back_at")
        batch.drop_column("recharge_posting_id")
