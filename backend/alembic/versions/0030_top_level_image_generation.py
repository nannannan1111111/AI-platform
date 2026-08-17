"""Allow account-owned image generation outside a canvas."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_top_level_image_generation"
down_revision: str | None = "0029_runninghub_user_price_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow generation tasks and their delivered media to omit a canvas."""
    with op.batch_alter_table("generation_tasks") as batch:
        batch.alter_column("canvas_id", existing_type=sa.String(length=255), nullable=True)
    with op.batch_alter_table("generated_media") as batch:
        batch.alter_column("canvas_id", existing_type=sa.String(length=36), nullable=True)
    op.create_table(
        "reference_media",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('temporary', 'expired')", name="ck_reference_media_state"),
        sa.CheckConstraint("size_bytes > 0", name="ck_reference_media_size_positive"),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_reference_media_hash_length"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reference_media_account_expiration",
        "reference_media",
        ["account_space_id", "expires_at", "id"],
    )


def downgrade() -> None:
    """Restore the original requirement that every generation belongs to a canvas."""
    op.drop_index("ix_reference_media_account_expiration", table_name="reference_media")
    op.drop_table("reference_media")
    with op.batch_alter_table("generated_media") as batch:
        batch.alter_column("canvas_id", existing_type=sa.String(length=36), nullable=False)
    with op.batch_alter_table("generation_tasks") as batch:
        batch.alter_column("canvas_id", existing_type=sa.String(length=255), nullable=False)
