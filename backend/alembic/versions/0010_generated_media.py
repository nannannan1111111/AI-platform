"""Persist account-owned temporary generated media metadata."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_generated_media"
down_revision: str | None = "0009_account_canvases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create durable metadata for generated media stored outside PostgreSQL."""
    op.create_table(
        "generated_media",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("result_reference", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('image', 'video', 'audio')", name="ck_generated_media_kind"),
        sa.CheckConstraint("state = 'temporary'", name="ck_generated_media_state"),
        sa.CheckConstraint("size_bytes > 0", name="ck_generated_media_size_positive"),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_generated_media_hash_length"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_space_id",
            "task_id",
            "result_reference",
            name="uq_generated_media_task_result",
        ),
    )
    op.create_index(
        "ix_generated_media_account_task_created",
        "generated_media",
        ["account_space_id", "task_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Remove generated media metadata."""
    op.drop_index("ix_generated_media_account_task_created", table_name="generated_media")
    op.drop_table("generated_media")
