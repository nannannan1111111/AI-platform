"""Persist image editing operation and input fidelity snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0052_generation_task_image_edit_options"
down_revision: str | None = "0051_account_generation_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add backward-compatible image editing request fields."""
    op.add_column(
        "generation_tasks",
        sa.Column("operation", sa.String(length=16), nullable=False, server_default="auto"),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("input_fidelity", sa.String(length=16), nullable=False, server_default="auto"),
    )


def downgrade() -> None:
    """Remove image editing request fields."""
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_column("input_fidelity")
        batch_op.drop_column("operation")
