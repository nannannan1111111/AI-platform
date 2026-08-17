"""Add ordinary recharge global ratio and allow non-package orders."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0057_global_recharge_rate"
down_revision: str | None = "0056_platform_payment_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist a singleton ratio and make the package reference optional."""
    op.create_table(
        "platform_recharge_settings",
        sa.Column("settings_key", sa.String(length=32), nullable=False),
        sa.Column("credits_per_cny_units", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "credits_per_cny_units > 0",
            name="ck_platform_recharge_rate_positive",
        ),
        sa.PrimaryKeyConstraint("settings_key"),
    )
    op.execute(
        sa.text(
            "INSERT INTO platform_recharge_settings "
            "(settings_key, credits_per_cny_units, updated_at) "
            "VALUES ('global', 10000, CURRENT_TIMESTAMP)"
        )
    )
    with op.batch_alter_table("recharge_orders") as batch_op:
        batch_op.alter_column(
            "package_version_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )


def downgrade() -> None:
    """Restore package-only orders after removing ordinary recharge data."""
    op.execute(sa.text("DELETE FROM recharge_orders WHERE package_version_id IS NULL"))
    with op.batch_alter_table("recharge_orders") as batch_op:
        batch_op.alter_column(
            "package_version_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
    op.drop_table("platform_recharge_settings")
