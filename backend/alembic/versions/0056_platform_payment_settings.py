"""Add administrator-managed EPay settings."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0056_platform_payment_settings"
down_revision: str | None = "0055_image_model_reference_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one global payment settings row without storing its merchant key."""
    op.create_table(
        "platform_payment_settings",
        sa.Column("settings_key", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("gateway_url", sa.String(length=1024), nullable=False),
        sa.Column("public_base_url", sa.String(length=1024), nullable=False),
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("merchant_key_secret_ref", sa.String(length=1024), nullable=True),
        sa.Column("methods_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("settings_key"),
    )


def downgrade() -> None:
    """Remove administrator-managed payment settings."""
    op.drop_table("platform_payment_settings")
