"""Persist released media state after its final canvas reference is removed."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_release_canvas_media"
down_revision: str | None = "0012_retain_generated_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow generated media to retain an auditable released state."""
    with op.batch_alter_table("generated_media") as batch:
        batch.drop_constraint("ck_generated_media_state", type_="check")
        batch.add_column(sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_generated_media_state",
            "state IN ('temporary', 'expired', 'persistent', 'released')",
        )


def downgrade() -> None:
    """Remove released records that the previous schema cannot represent."""
    op.execute(sa.text("DELETE FROM generated_media WHERE state = 'released'"))
    with op.batch_alter_table("generated_media") as batch:
        batch.drop_constraint("ck_generated_media_state", type_="check")
        batch.drop_column("released_at")
        batch.create_check_constraint(
            "ck_generated_media_state",
            "state IN ('temporary', 'expired', 'persistent')",
        )
