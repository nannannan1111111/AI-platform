"""Persist generated-media deletion tombstones for the 24-hour workspace."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035_generated_media_workspace_lifecycle"
down_revision: str | None = "0034_generation_task_output_options"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record explicit user deletion without removing task history."""
    with op.batch_alter_table("generated_media") as batch_op:
        batch_op.drop_constraint("ck_generated_media_state", type_="check")
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column(
            "task_id",
            existing_type=sa.String(length=255),
            nullable=True,
        )
        batch_op.create_check_constraint(
            "ck_generated_media_state",
            "state IN ('temporary', 'expired', 'persistent', 'released', 'deleted')",
        )
    with op.batch_alter_table("reference_media") as batch_op:
        batch_op.drop_constraint("ck_reference_media_state", type_="check")
        batch_op.create_check_constraint(
            "ck_reference_media_state",
            "state IN ('temporary', 'expired', 'deleted')",
        )


def downgrade() -> None:
    """Remove the explicit deletion timestamp."""
    op.execute(sa.text("DELETE FROM generated_media WHERE state = 'deleted'"))
    op.execute(sa.text("DELETE FROM reference_media WHERE state = 'deleted'"))
    op.execute(
        sa.text(
            "DELETE FROM canvas_media_references WHERE media_id IN "
            "(SELECT id FROM generated_media WHERE task_id IS NULL)"
        )
    )
    op.execute(sa.text("DELETE FROM generated_media WHERE task_id IS NULL"))
    with op.batch_alter_table("generated_media") as batch_op:
        batch_op.drop_constraint("ck_generated_media_state", type_="check")
        batch_op.drop_column("deleted_at")
        batch_op.alter_column(
            "task_id",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_generated_media_state",
            "state IN ('temporary', 'expired', 'persistent', 'released')",
        )
    with op.batch_alter_table("reference_media") as batch_op:
        batch_op.drop_constraint("ck_reference_media_state", type_="check")
        batch_op.create_check_constraint(
            "ck_reference_media_state",
            "state IN ('temporary', 'expired')",
        )
