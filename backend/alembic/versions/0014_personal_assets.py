"""Persist account-owned personal assets and idempotent saves."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_personal_assets"
down_revision: str | None = "0013_release_canvas_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create pending and active personal asset records."""
    op.create_table(
        "personal_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('pending', 'active')", name="ck_personal_assets_state"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["media_id"], ["generated_media.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_space_id", "idempotency_key", name="uq_personal_assets_idempotency"),
    )
    op.create_index(
        "ix_personal_assets_account_state_created",
        "personal_assets",
        ["account_space_id", "state", "created_at"],
    )


def downgrade() -> None:
    """Remove personal assets."""
    op.drop_index("ix_personal_assets_account_state_created", table_name="personal_assets")
    op.drop_table("personal_assets")
