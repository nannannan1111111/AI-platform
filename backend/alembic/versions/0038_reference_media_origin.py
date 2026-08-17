"""Separate canvas-only references from the standalone image workspace."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038_reference_media_origin"
down_revision: str | None = "0037_generation_task_mask_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record where each reference was created; existing references remain standalone."""
    with op.batch_alter_table("reference_media") as batch_op:
        batch_op.add_column(
            sa.Column("origin", sa.String(length=16), nullable=False, server_default="standalone")
        )
        batch_op.create_check_constraint(
            "ck_reference_media_origin",
            "origin IN ('standalone', 'canvas')",
        )


def downgrade() -> None:
    """Remove reference-media origin metadata."""
    with op.batch_alter_table("reference_media") as batch_op:
        batch_op.drop_constraint("ck_reference_media_origin", type_="check")
        batch_op.drop_column("origin")
