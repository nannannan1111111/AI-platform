"""Allow model prices to leave the active catalog without losing audit history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_model_price_deletion_tombstones"
down_revision: str | None = "0042_active_route_mapping_uniqueness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_price_versions") as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_price_versions") as batch_op:
        batch_op.drop_column("deleted_at")
