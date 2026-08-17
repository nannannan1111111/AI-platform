"""Persist the explicit OpenAI Images size in generation task snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033_generation_task_image_size"
down_revision: str | None = "0032_admin_user_credit_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an empty-compatible size snapshot for historical generation tasks."""
    op.add_column(
        "generation_tasks",
        sa.Column("size", sa.String(length=32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    """Remove the explicit image size snapshot."""
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_column("size")
