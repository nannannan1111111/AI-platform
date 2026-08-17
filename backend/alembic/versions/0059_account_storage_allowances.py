"""Persist administrator-managed per-account storage allowances."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0059_account_storage_allowances"
down_revision: str | None = "0058_generation_task_deadline_setting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create sparse per-account overrides that take priority over the global allowance."""
    op.create_table(
        "account_storage_allowances",
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("limit_bytes", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_space_id"],
            ["account_spaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_space_id"),
        sa.CheckConstraint(
            "limit_bytes >= 0",
            name="ck_account_storage_allowances_limit_nonnegative",
        ),
    )


def downgrade() -> None:
    """Remove all per-account storage allowance overrides."""
    op.drop_table("account_storage_allowances")
