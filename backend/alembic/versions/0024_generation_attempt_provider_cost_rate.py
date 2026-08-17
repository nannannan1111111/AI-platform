"""Freeze the effective Provider cost version on new generation attempts."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_generation_attempt_provider_cost_rate"
down_revision: str | None = "0023_provider_cost_rates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Link attempts to immutable Provider cost versions while preserving legacy rows."""
    with op.batch_alter_table("image_generation_attempts") as batch:
        batch.add_column(sa.Column("provider_cost_rate_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_generation_attempts_provider_cost_rate",
            "provider_cost_rates",
            ["provider_cost_rate_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    """Remove the optional Provider cost version link from generation attempts."""
    with op.batch_alter_table("image_generation_attempts") as batch:
        batch.drop_constraint("fk_generation_attempts_provider_cost_rate", type_="foreignkey")
        batch.drop_column("provider_cost_rate_id")
