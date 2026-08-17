"""增加版本化充值包与可审计额度账务记录。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_versioned_credits"
down_revision: str | None = "0003_verify_user_emails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建充值包版本和额度账务记录表。"""
    op.create_table(
        "recharge_package_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_code", sa.String(length=64), nullable=False),
        sa.Column("payment_cny_units", sa.BigInteger(), nullable=False),
        sa.Column("credit_units", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("payment_cny_units > 0", name="ck_recharge_package_payment_positive"),
        sa.CheckConstraint("credit_units > 0", name="ck_recharge_package_credits_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_code", "effective_from", name="uq_recharge_package_code_effective_from"),
    )
    op.create_table(
        "credit_postings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("available_delta_units", sa.BigInteger(), nullable=False),
        sa.Column("available_units_after", sa.BigInteger(), nullable=False),
        sa.Column("package_version_id", sa.String(length=36), nullable=True),
        sa.Column("reference", sa.String(length=255), nullable=False),
        sa.Column("reverses_posting_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('recharge', 'reversal')", name="ck_credit_posting_kind"),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["package_version_id"], ["recharge_package_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reverses_posting_id"], ["credit_postings.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_space_id", "sequence_number", name="uq_credit_posting_account_sequence"),
        sa.UniqueConstraint("reference"),
        sa.UniqueConstraint("reverses_posting_id"),
    )


def downgrade() -> None:
    """按依赖逆序删除额度账务记录和充值包版本。"""
    op.drop_table("credit_postings")
    op.drop_table("recharge_package_versions")
