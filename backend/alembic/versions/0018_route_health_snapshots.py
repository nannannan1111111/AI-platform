"""Persist rolling availability and latency snapshots for model routes."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_route_health_snapshots"
down_revision: str | None = "0017_model_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one rolling health snapshot per image model route."""
    op.create_table(
        "route_health_snapshots",
        sa.Column("route_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_latency_ms", sa.Integer(), nullable=False),
        sa.Column("ewma_latency_ms", sa.Integer(), nullable=False),
        sa.Column("p95_latency_ms", sa.Integer(), nullable=False),
        sa.Column("successful_checks", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("latency_samples", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["route_id"], ["image_model_routes.id"]),
        sa.PrimaryKeyConstraint("route_id"),
    )


def downgrade() -> None:
    """Remove route health snapshots."""
    op.drop_table("route_health_snapshots")
