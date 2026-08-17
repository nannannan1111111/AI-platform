"""Index account-scoped recent generation task history."""

from collections.abc import Sequence

from alembic import op

revision: str = "0026_generation_task_account_history_index"
down_revision: str | None = "0025_canvas_deletion_tombstones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Support newest-first generation history within one account space."""
    op.create_index(
        "ix_generation_tasks_account_created_id",
        "generation_tasks",
        ["account_space_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Remove the account-scoped generation history index."""
    op.drop_index("ix_generation_tasks_account_created_id", table_name="generation_tasks")
