"""Make the generation task deadline administrator-managed."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0058_generation_task_deadline_setting"
down_revision: str | None = "0057_global_recharge_rate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Default the post-dispatch task deadline to ten minutes."""
    op.add_column(
        "generation_worker_capacity",
        sa.Column("task_deadline_minutes", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE generation_worker_capacity "
            "SET task_deadline_minutes = 10 "
            "WHERE task_deadline_minutes IS NULL"
        )
    )
    with op.batch_alter_table("generation_worker_capacity") as batch:
        batch.alter_column("task_deadline_minutes", nullable=False)
        batch.create_check_constraint(
            "ck_worker_capacity_task_deadline_minutes",
            "task_deadline_minutes BETWEEN 1 AND 120",
        )


def downgrade() -> None:
    """Remove the administrator-managed generation deadline."""
    with op.batch_alter_table("generation_worker_capacity") as batch:
        batch.drop_constraint("ck_worker_capacity_task_deadline_minutes", type_="check")
        batch.drop_column("task_deadline_minutes")
