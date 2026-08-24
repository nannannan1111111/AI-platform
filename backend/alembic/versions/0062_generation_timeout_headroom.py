"""Give paid image requests enough time to return before task expiry."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0062_generation_timeout_headroom"
down_revision: str | None = "0061_password_reset_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Raise only untouched legacy defaults, preserving administrator overrides."""
    op.execute(
        sa.text(
            "UPDATE api_providers "
            "SET request_timeout_seconds = 1200 "
            "WHERE request_timeout_seconds = 600"
        )
    )
    op.execute(
        sa.text(
            "UPDATE generation_worker_capacity "
            "SET task_deadline_minutes = 30 "
            "WHERE task_deadline_minutes = 10"
        )
    )


def downgrade() -> None:
    """Keep operational timeout choices when downgrading code."""
