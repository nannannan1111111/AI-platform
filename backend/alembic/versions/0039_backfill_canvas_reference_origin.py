"""Backfill origin for references created by the legacy canvas conversion endpoint."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039_backfill_canvas_reference_origin"
down_revision: str | None = "0038_reference_media_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Hide still-live legacy canvas conversions from standalone recents."""
    op.execute(
        sa.text(
            "UPDATE reference_media SET origin = 'canvas' "
            "WHERE origin = 'standalone' AND original_name LIKE 'generated-%'"
        )
    )


def downgrade() -> None:
    """Restore the pre-backfill visibility of legacy converted references."""
    op.execute(
        sa.text(
            "UPDATE reference_media SET origin = 'standalone' "
            "WHERE origin = 'canvas' AND original_name LIKE 'generated-%'"
        )
    )
