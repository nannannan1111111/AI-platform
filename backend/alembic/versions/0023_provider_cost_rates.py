"""Persist immutable Provider cost rate versions."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_provider_cost_rates"
down_revision: str | None = "0022_generation_task_request_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create versioned per-route Provider image costs."""
    op.create_table(
        "provider_cost_rates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("image_model_route_id", sa.String(length=36), nullable=False),
        sa.Column("variant_code", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provider_currency", sa.String(length=3), nullable=False),
        sa.Column("cost_per_image_micros", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cost_per_image_micros >= 0", name="ck_provider_cost_rates_nonnegative_cost"),
        sa.ForeignKeyConstraint(["image_model_route_id"], ["image_model_routes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "image_model_route_id",
            "variant_code",
            "version",
            name="uq_provider_cost_rates_version",
        ),
        sa.UniqueConstraint(
            "image_model_route_id",
            "variant_code",
            "effective_from",
            name="uq_provider_cost_rates_effective_time",
        ),
    )


def downgrade() -> None:
    """Remove Provider cost rate versions."""
    op.drop_table("provider_cost_rates")
