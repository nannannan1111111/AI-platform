"""增加模型价格版本和生成额度冻结生命周期。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_model_prices_and_generation_freezes"
down_revision: str | None = "0004_versioned_credits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建模型价格与额度冻结表，并扩展账务记录快照字段。"""
    op.create_table(
        "model_price_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("logical_model", sa.String(length=128), nullable=False),
        sa.Column("output_spec", sa.String(length=128), nullable=False),
        sa.Column("credit_units", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("credit_units > 0", name="ck_model_price_credits_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "logical_model",
            "output_spec",
            "effective_from",
            name="uq_model_price_spec_effective_from",
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO model_price_versions "
            "(id, logical_model, output_spec, credit_units, effective_from, published_at) "
            "VALUES (:id, :logical_model, :output_spec, :credit_units, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ).bindparams(
            id="00000000-0000-0000-0000-000000000005",
            logical_model="gpt-image-2",
            output_spec="4k",
            credit_units=1500,
        )
    )
    op.create_table(
        "credit_freezes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("task_reference", sa.String(length=255), nullable=False),
        sa.Column("model_price_version_id", sa.String(length=36), nullable=False),
        sa.Column("logical_model", sa.String(length=128), nullable=False),
        sa.Column("output_spec", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_units", sa.BigInteger(), nullable=False),
        sa.Column("frozen_units", sa.BigInteger(), nullable=False),
        sa.Column("available_units_after", sa.BigInteger(), nullable=False),
        sa.Column("frozen_units_after", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_credit_freeze_quantity_positive"),
        sa.CheckConstraint("unit_price_units > 0", name="ck_credit_freeze_unit_price_positive"),
        sa.CheckConstraint("frozen_units > 0", name="ck_credit_freeze_amount_positive"),
        sa.CheckConstraint("status IN ('active', 'settled', 'released')", name="ck_credit_freeze_status"),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_price_version_id"], ["model_price_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_reference"),
    )
    op.add_column(
        "credit_postings",
        sa.Column("frozen_delta_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "credit_postings",
        sa.Column("frozen_units_after", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("credit_postings", sa.Column("model_price_version_id", sa.String(length=36), nullable=True))
    op.add_column("credit_postings", sa.Column("generation_reference", sa.String(length=255), nullable=True))
    with op.batch_alter_table("credit_postings") as batch_op:
        batch_op.drop_constraint("ck_credit_posting_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_posting_kind",
            "kind IN ('recharge', 'reversal', 'freeze', 'settlement', 'release')",
        )


def downgrade() -> None:
    """移除生成额度字段、冻结表和模型价格版本。"""
    with op.batch_alter_table("credit_postings") as batch_op:
        batch_op.drop_constraint("ck_credit_posting_kind", type_="check")
        batch_op.create_check_constraint("ck_credit_posting_kind", "kind IN ('recharge', 'reversal')")
    op.drop_column("credit_postings", "generation_reference")
    op.drop_column("credit_postings", "model_price_version_id")
    op.drop_column("credit_postings", "frozen_units_after")
    op.drop_column("credit_postings", "frozen_delta_units")
    op.drop_table("credit_freezes")
    op.drop_table("model_price_versions")
