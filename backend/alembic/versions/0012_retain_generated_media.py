"""Persist retained media state and canvas references."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_retain_generated_media"
down_revision: str | None = "0011_expire_generated_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow persistent media and record its owning canvas reference."""
    with op.batch_alter_table("generated_media") as batch:
        batch.drop_constraint("ck_generated_media_state", type_="check")
        batch.alter_column(
            "expires_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch.add_column(sa.Column("retained_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_generated_media_state",
            "state IN ('temporary', 'expired', 'persistent')",
        )
    op.create_index(
        "ix_generated_media_account_state_hash",
        "generated_media",
        ["account_space_id", "state", "content_hash"],
    )
    op.create_table(
        "canvas_media_references",
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["media_id"], ["generated_media.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("account_space_id", "canvas_id", "media_id"),
    )
    op.create_index(
        "ix_canvas_media_references_media",
        "canvas_media_references",
        ["media_id"],
    )


def downgrade() -> None:
    """Remove retained records that the previous schema cannot represent."""
    op.drop_index("ix_canvas_media_references_media", table_name="canvas_media_references")
    op.drop_table("canvas_media_references")
    op.execute(sa.text("DELETE FROM generated_media WHERE state = 'persistent'"))
    op.drop_index("ix_generated_media_account_state_hash", table_name="generated_media")
    with op.batch_alter_table("generated_media") as batch:
        batch.drop_constraint("ck_generated_media_state", type_="check")
        batch.drop_column("retained_at")
        batch.alter_column(
            "expires_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_generated_media_state",
            "state IN ('temporary', 'expired')",
        )
