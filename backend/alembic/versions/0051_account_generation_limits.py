"""Persist administrator-managed per-account generation concurrency."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051_account_generation_limits"
down_revision: str | None = "0050_platform_email_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create sparse per-account overrides; absent accounts default to two."""
    op.create_table(
        "account_generation_limits",
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("execution_concurrency", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_space_id"],
            ["account_spaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_space_id"),
        sa.CheckConstraint(
            "execution_concurrency BETWEEN 1 AND 20",
            name="ck_account_generation_execution_concurrency",
        ),
    )


def downgrade() -> None:
    op.drop_table("account_generation_limits")
