"""Index the durable generation queue for bounded worker polling."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_generation_queue_index"
down_revision: str | None = "0043_model_price_deletion_tombstones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_generation_tasks_queue",
        "generation_tasks",
        ["status", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_tasks_queue", table_name="generation_tasks")
