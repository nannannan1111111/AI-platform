"""Persist short-lived, single-use password-reset token digests."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061_password_reset_tokens"
down_revision: str | None = "0060_auth_abuse_protection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one active digest-only password-reset token per user."""
    op.create_table(
        "password_reset_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
        sa.UniqueConstraint("user_id", name="uq_password_reset_tokens_user"),
    )


def downgrade() -> None:
    """Remove password-reset token state without changing user credentials."""
    op.drop_table("password_reset_tokens")
