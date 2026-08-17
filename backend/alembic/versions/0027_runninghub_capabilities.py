"""Persist administrator-published RunningHub capabilities."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_runninghub_capabilities"
down_revision: str | None = "0026_generation_task_account_history_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the RunningHub capability control-plane catalog."""
    op.create_table(
        "runninghub_capabilities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("input_capabilities", sa.JSON(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove the RunningHub capability control-plane catalog."""
    op.drop_table("runninghub_capabilities")
