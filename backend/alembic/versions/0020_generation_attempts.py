"""Persist the first generation attempt before provider submission."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_generation_attempts"
down_revision: str | None = "0019_routing_policies_and_task_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create durable generation attempt identities and idempotency keys."""
    op.create_table(
        "image_generation_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("generation_task_id", sa.String(length=255), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.String(length=36), nullable=False),
        sa.Column("provider_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_no > 0", name="ck_generation_attempt_number_positive"),
        sa.CheckConstraint("status = 'created'", name="ck_generation_attempt_status"),
        sa.ForeignKeyConstraint(["generation_task_id"], ["generation_tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["route_id"], ["image_model_routes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generation_task_id",
            "attempt_no",
            name="uq_generation_attempts_task_number",
        ),
        sa.UniqueConstraint("provider_idempotency_key"),
    )


def downgrade() -> None:
    """Remove generation attempts before their tasks or routes."""
    op.drop_table("image_generation_attempts")
