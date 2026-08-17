"""Track provider dispatch time for per-user queued generation."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047_user_generation_queue"
down_revision: str | None = "0046_generation_worker_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the timestamp that starts the provider-processing deadline."""
    op.add_column("generation_tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_generation_tasks_active_account_started",
        "generation_tasks",
        ["account_space_id", "status", "started_at", "created_at"],
    )


def downgrade() -> None:
    """Remove provider dispatch timing."""
    op.drop_index("ix_generation_tasks_active_account_started", table_name="generation_tasks")
    op.drop_column("generation_tasks", "started_at")
