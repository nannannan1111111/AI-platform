"""Persist irreversible Provider and model-route deletion tombstones."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036_model_routing_deletion_tombstones"
down_revision: str | None = "0035_generated_media_workspace_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable deletion times without rewriting active configuration."""
    with op.batch_alter_table("api_providers") as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("image_model_routes") as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove route tombstones before Provider tombstones."""
    with op.batch_alter_table("image_model_routes") as batch_op:
        batch_op.drop_column("deleted_at")
    with op.batch_alter_table("api_providers") as batch_op:
        batch_op.drop_column("deleted_at")
