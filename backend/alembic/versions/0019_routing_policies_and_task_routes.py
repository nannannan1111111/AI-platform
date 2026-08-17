"""Persist routing policies and selected routes on generation tasks."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_routing_policies_and_task_routes"
down_revision: str | None = "0018_route_health_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create routing policies and preserve each task's initial selection."""
    op.create_table(
        "image_routing_policies",
        sa.Column("logical_model", sa.String(length=128), nullable=False),
        sa.Column("output_spec", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("preferred_route_id", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("logical_model", "output_spec"),
    )
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.add_column(sa.Column("selected_route_id", sa.String(length=36), server_default="", nullable=False))
        batch_op.add_column(
            sa.Column("route_selection_reason", sa.String(length=32), server_default="", nullable=False)
        )


def downgrade() -> None:
    """Remove task selections before routing policies."""
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_column("route_selection_reason")
        batch_op.drop_column("selected_route_id")
    op.drop_table("image_routing_policies")
