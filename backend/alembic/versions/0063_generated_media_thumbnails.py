"""Track account-owned generated-media thumbnail objects."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0063_generated_media_thumbnails"
down_revision: str | None = "0062_generation_timeout_headroom"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable metadata so existing images can be backfilled on first read."""
    op.add_column("generated_media", sa.Column("thumbnail_object_key", sa.String(length=1024), nullable=True))
    op.add_column("generated_media", sa.Column("thumbnail_mime_type", sa.String(length=128), nullable=True))
    op.add_column("generated_media", sa.Column("thumbnail_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("generated_media", sa.Column("thumbnail_content_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop thumbnail metadata; object lifecycle remains externally managed."""
    op.drop_column("generated_media", "thumbnail_content_hash")
    op.drop_column("generated_media", "thumbnail_size_bytes")
    op.drop_column("generated_media", "thumbnail_mime_type")
    op.drop_column("generated_media", "thumbnail_object_key")
