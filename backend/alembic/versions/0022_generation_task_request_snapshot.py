"""Persist immutable generation task request snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_generation_task_request_snapshot"
down_revision: str | None = "0021_generation_attempt_submission_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the user prompt and selected aspect ratio to generation tasks."""
    with op.batch_alter_table("generation_tasks") as batch:
        batch.add_column(sa.Column("prompt", sa.Text(), server_default="", nullable=False))
        batch.add_column(sa.Column("aspect_ratio", sa.String(length=8), server_default="1:1", nullable=False))


def downgrade() -> None:
    """Remove generation request snapshots."""
    with op.batch_alter_table("generation_tasks") as batch:
        batch.drop_column("aspect_ratio")
        batch.drop_column("prompt")
