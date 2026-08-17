"""Persist SaaS generation task ownership and lifecycle."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_generation_tasks"
down_revision: str | None = "0005_model_prices_and_generation_freezes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the durable generation task write model."""
    op.create_table(
        "generation_tasks",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=255), nullable=False),
        sa.Column("logical_model", sa.String(length=128), nullable=False),
        sa.Column("output_spec", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("credit_freeze_id", sa.String(length=36), nullable=False),
        sa.Column("model_price_version_id", sa.String(length=36), nullable=False),
        sa.Column("frozen_units", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_task_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("delivered_quantity", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("outcome_reference", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_generation_task_quantity_positive"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_generation_task_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["credit_freeze_id"], ["credit_freezes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_price_version_id"], ["model_price_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_space_id", "id", name="uq_generation_task_account_id"),
        sa.UniqueConstraint("credit_freeze_id", name="uq_generation_task_freeze"),
    )


def downgrade() -> None:
    """Remove the generation task write model."""
    op.drop_table("generation_tasks")
