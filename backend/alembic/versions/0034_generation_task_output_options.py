"""Persist image resolution tiers and requested output formats."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_generation_task_output_options"
down_revision: str | None = "0033_generation_task_image_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add empty-compatible request fields for historical generation tasks."""
    op.add_column(
        "generation_tasks",
        sa.Column("resolution_tier", sa.String(length=16), nullable=False, server_default=""),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("output_format", sa.String(length=16), nullable=False, server_default=""),
    )


def downgrade() -> None:
    """Remove the output-tier and format request snapshots."""
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_column("output_format")
        batch_op.drop_column("resolution_tier")
