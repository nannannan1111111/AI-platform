"""Persist irreversible canvas deletion tombstones."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_canvas_deletion_tombstones"
down_revision: str | None = "0024_generation_attempt_provider_cost_rate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow canvas contents to be scrubbed while historical foreign keys remain valid."""
    with op.batch_alter_table("canvases") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(
            "ix_canvases_account_deleted_created",
            ["account_space_id", "deleted_at", "created_at", "id"],
        )


def downgrade() -> None:
    """Remove canvas deletion tombstone support."""
    with op.batch_alter_table("canvases") as batch:
        batch.drop_index("ix_canvases_account_deleted_created")
        batch.drop_column("deleted_at")
