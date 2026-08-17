"""Persist image quality and temporary reference media in generation snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031_generation_task_reference_snapshot"
down_revision: str | None = "0030_top_level_image_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add immutable quality and reference-media identifiers to generation tasks."""
    with op.batch_alter_table("generation_tasks") as batch:
        batch.add_column(
            sa.Column(
                "quality",
                sa.String(length=16),
                nullable=False,
                server_default="auto",
            )
        )
        batch.add_column(
            sa.Column(
                "reference_media_ids",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    """Remove the extended generation request snapshot fields."""
    with op.batch_alter_table("generation_tasks") as batch:
        batch.drop_column("reference_media_ids")
        batch.drop_column("quality")
