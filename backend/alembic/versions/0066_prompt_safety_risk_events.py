"""Add prompt safety settings and runtime risk events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_prompt_safety_risk_events"
down_revision: str | None = "0065_merge_v17_redeem_concurrency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_safety_settings",
        sa.Column("settings_key", sa.String(length=32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("prompt_check_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("keywords", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "risk_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_risk_events_occurred_at", "risk_events", ["occurred_at"])
    op.create_table(
        "risk_event_counters",
        sa.Column("counter_key", sa.String(length=64), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("risk_event_counters")
    op.drop_index("ix_risk_events_occurred_at", table_name="risk_events")
    op.drop_table("risk_events")
    op.drop_table("prompt_safety_settings")
