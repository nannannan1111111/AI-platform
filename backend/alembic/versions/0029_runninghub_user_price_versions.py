"""Persist immutable RunningHub user price versions."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_runninghub_user_price_versions"
down_revision: str | None = "0028_runninghub_input_schema_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create effective-dated RunningHub user price versions."""
    op.create_table(
        "runninghub_user_price_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("capability_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("credit_units", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["capability_id"], ["runninghub_capabilities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability_id", "version", name="uq_runninghub_user_price_version"),
        sa.UniqueConstraint(
            "capability_id",
            "effective_from",
            name="uq_runninghub_user_price_effective_from",
        ),
    )


def downgrade() -> None:
    """Remove RunningHub user price versions."""
    op.drop_table("runninghub_user_price_versions")
