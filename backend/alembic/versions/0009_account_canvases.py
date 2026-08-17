"""Persist account-owned versioned canvases."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_account_canvases"
down_revision: str | None = "0008_recharge_order_chargebacks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the durable account-owned canvas write model."""
    op.create_table(
        "canvases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('classic', 'smart')", name="ck_canvas_kind"),
        sa.CheckConstraint("version > 0", name="ck_canvas_version_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_canvases_account_created", "canvases", ["account_space_id", "created_at", "id"])


def downgrade() -> None:
    """Remove the account-owned canvas write model."""
    op.drop_index("ix_canvases_account_created", table_name="canvases")
    op.drop_table("canvases")
