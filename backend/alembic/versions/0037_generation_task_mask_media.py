"""Persist a generation task's separate mask media identity."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037_generation_task_mask_media"
down_revision: str | None = "0036_model_routing_deletion_tombstones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an empty-compatible mask snapshot for historical generation tasks."""
    op.add_column(
        "generation_tasks",
        sa.Column("mask_media_id", sa.String(length=255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    """Remove the separate mask media snapshot."""
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_column("mask_media_id")
