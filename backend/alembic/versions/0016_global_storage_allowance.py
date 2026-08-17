"""Persist one storage allowance shared by all personal account spaces."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_global_storage_allowance"
down_revision: str | None = "0015_remove_personal_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create and seed the global storage allowance policy."""
    table = op.create_table(
        "storage_allowance_policies",
        sa.Column("policy_key", sa.String(length=32), nullable=False),
        sa.Column("limit_bytes", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("limit_bytes >= 0", name="ck_storage_allowance_policies_limit_nonnegative"),
        sa.PrimaryKeyConstraint("policy_key"),
    )
    op.bulk_insert(table, [{"policy_key": "global", "limit_bytes": 0}])


def downgrade() -> None:
    """Remove the global storage allowance policy."""
    op.drop_table("storage_allowance_policies")
