"""Persist generation attempt provider-submission states."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_generation_attempt_submission_states"
down_revision: str | None = "0020_generation_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUBMISSION_STATES = "'created', 'submitting', 'provider_pending', 'unknown', 'failed'"


def upgrade() -> None:
    """Add immutable submission outcome details and expand attempt states."""
    with op.batch_alter_table("image_generation_attempts") as batch:
        batch.drop_constraint("ck_generation_attempt_status", type_="check")
        batch.add_column(sa.Column("provider_task_id", sa.String(length=255), server_default="", nullable=False))
        batch.add_column(sa.Column("error_code", sa.String(length=128), server_default="", nullable=False))
        batch.add_column(sa.Column("error", sa.String(length=1024), server_default="", nullable=False))
        batch.add_column(sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_generation_attempt_status",
            f"status IN ({_SUBMISSION_STATES})",
        )


def downgrade() -> None:
    """Return attempts to their pre-submission created state."""
    with op.batch_alter_table("image_generation_attempts") as batch:
        batch.drop_constraint("ck_generation_attempt_status", type_="check")
    op.execute(sa.text("UPDATE image_generation_attempts SET status = 'created'"))
    with op.batch_alter_table("image_generation_attempts") as batch:
        batch.drop_column("finished_at")
        batch.drop_column("accepted_at")
        batch.drop_column("submitted_at")
        batch.drop_column("error")
        batch.drop_column("error_code")
        batch.drop_column("provider_task_id")
        batch.create_check_constraint("ck_generation_attempt_status", "status = 'created'")
