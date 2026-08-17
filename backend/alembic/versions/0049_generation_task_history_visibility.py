"""Allow users to hide terminal generation task history."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0049_generation_task_history_visibility"
down_revision: str | None = "0048_global_generation_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a non-destructive history visibility marker to generation tasks."""
    op.add_column(
        "generation_tasks",
        sa.Column("history_hidden_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Restore all generation task history visibility."""
    op.drop_column("generation_tasks", "history_hidden_at")
