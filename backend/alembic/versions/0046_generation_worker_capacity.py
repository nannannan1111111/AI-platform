"""Persist administrator-managed image generation worker capacity."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0046_generation_worker_capacity"
down_revision: str | None = "0045_provider_transport_and_concurrency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create and seed the singleton generation worker capacity setting."""
    table = op.create_table(
        "generation_worker_capacity",
        sa.Column("settings_key", sa.String(length=32), nullable=False),
        sa.Column("enabled_workers", sa.Integer(), nullable=False),
        sa.Column("concurrency_per_worker", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("enabled_workers BETWEEN 1 AND 64", name="ck_worker_capacity_workers"),
        sa.CheckConstraint("concurrency_per_worker BETWEEN 1 AND 50", name="ck_worker_capacity_concurrency"),
        sa.PrimaryKeyConstraint("settings_key"),
    )
    op.bulk_insert(
        table,
        [{
            "settings_key": "global",
            "enabled_workers": 4,
            "concurrency_per_worker": 5,
            "updated_at": datetime.now(UTC),
        }],
    )


def downgrade() -> None:
    """Remove the generation worker capacity setting."""
    op.drop_table("generation_worker_capacity")
