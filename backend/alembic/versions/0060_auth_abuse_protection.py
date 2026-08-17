"""Persist privacy-preserving authentication rate-limit windows."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060_auth_abuse_protection"
down_revision: str | None = "0059_account_storage_allowances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create atomic fixed windows shared by all HTTP workers."""
    op.create_table(
        "auth_rate_limit_windows",
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("subject_scope", sa.String(length=32), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "action",
            "subject_scope",
            "subject_hash",
            "window_seconds",
            "window_started_at",
        ),
        sa.CheckConstraint("request_count > 0", name="ck_auth_rate_limit_windows_count_positive"),
        sa.CheckConstraint("window_seconds > 0", name="ck_auth_rate_limit_windows_seconds_positive"),
        sa.CheckConstraint("window_ends_at > window_started_at", name="ck_auth_rate_limit_windows_ordered"),
    )
    op.create_index(
        "ix_auth_rate_limit_windows_window_ends_at",
        "auth_rate_limit_windows",
        ["window_ends_at"],
    )


def downgrade() -> None:
    """Remove all transient authentication rate-limit windows."""
    op.drop_index("ix_auth_rate_limit_windows_window_ends_at", table_name="auth_rate_limit_windows")
    op.drop_table("auth_rate_limit_windows")
