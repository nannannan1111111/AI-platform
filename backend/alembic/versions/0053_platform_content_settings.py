"""Persist administrator-managed announcement and support content."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_platform_content_settings"
down_revision: str | None = "0052_generation_task_image_edit_options"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_content_settings",
        sa.Column("settings_key", sa.String(length=32), primary_key=True),
        sa.Column("announcement_text", sa.Text(), nullable=False),
        sa.Column("announcement_image_object_key", sa.String(length=1024), nullable=True),
        sa.Column("announcement_image_mime", sa.String(length=64), nullable=True),
        sa.Column("support_text", sa.Text(), nullable=False),
        sa.Column("support_image_object_key", sa.String(length=1024), nullable=True),
        sa.Column("support_image_mime", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_content_settings")
