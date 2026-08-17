"""Persist immutable RunningHub capability input schema versions."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_runninghub_input_schema_versions"
down_revision: str | None = "0027_runninghub_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create immutable input schema versions for RunningHub capabilities."""
    op.create_table(
        "runninghub_input_schema_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("capability_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["capability_id"], ["runninghub_capabilities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability_id", "version", name="uq_runninghub_input_schema_version"),
    )


def downgrade() -> None:
    """Remove RunningHub capability input schema versions."""
    op.drop_table("runninghub_input_schema_versions")
