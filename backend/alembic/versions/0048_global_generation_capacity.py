"""Add an administrator-managed global active image-unit limit."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0048_global_generation_capacity"
down_revision: str | None = "0047_user_generation_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Default the global queued-plus-running capacity to 500 images."""
    op.add_column(
        "generation_worker_capacity",
        sa.Column("global_active_image_limit", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE generation_worker_capacity "
            "SET global_active_image_limit = 500 "
            "WHERE global_active_image_limit IS NULL"
        )
    )
    with op.batch_alter_table("generation_worker_capacity") as batch:
        batch.alter_column("global_active_image_limit", nullable=False)
        batch.create_check_constraint(
            "ck_worker_capacity_global_active_images",
            "global_active_image_limit BETWEEN 1 AND 100000",
        )


def downgrade() -> None:
    """Remove the global image-unit capacity setting."""
    with op.batch_alter_table("generation_worker_capacity") as batch:
        batch.drop_constraint("ck_worker_capacity_global_active_images", type_="check")
        batch.drop_column("global_active_image_limit")
