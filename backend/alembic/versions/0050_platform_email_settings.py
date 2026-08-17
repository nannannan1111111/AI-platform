"""Add administrator-managed platform email settings."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050_platform_email_settings"
down_revision: str | None = "0049_generation_task_history_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one global SMTP settings row without storing its password."""
    op.create_table(
        "platform_email_settings",
        sa.Column("settings_key", sa.String(length=32), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False),
        sa.Column("public_base_url", sa.String(length=1024), nullable=False),
        sa.Column("smtp_host", sa.String(length=255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_sender", sa.String(length=320), nullable=False),
        sa.Column("smtp_username", sa.String(length=320), nullable=False),
        sa.Column("smtp_password_secret_ref", sa.String(length=1024), nullable=True),
        sa.Column("smtp_security", sa.String(length=16), nullable=False),
        sa.Column("smtp_timeout_seconds", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("settings_key"),
        sa.CheckConstraint("smtp_port BETWEEN 1 AND 65535", name="ck_platform_email_smtp_port"),
        sa.CheckConstraint(
            "smtp_timeout_seconds BETWEEN 1 AND 120",
            name="ck_platform_email_smtp_timeout",
        ),
    )


def downgrade() -> None:
    """Remove administrator-managed email settings."""
    op.drop_table("platform_email_settings")
