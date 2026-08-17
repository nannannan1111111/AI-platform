"""Allow generated media metadata to retain an expired state."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_expire_generated_media"
down_revision: str | None = "0010_generated_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extend generated media state without rewriting the 0010 migration."""
    with op.batch_alter_table("generated_media") as batch:
        batch.drop_constraint("ck_generated_media_state", type_="check")
        batch.create_check_constraint(
            "ck_generated_media_state",
            "state IN ('temporary', 'expired')",
        )


def downgrade() -> None:
    """Remove metadata that the previous schema cannot represent."""
    op.execute(sa.text("DELETE FROM generated_media WHERE state = 'expired'"))
    with op.batch_alter_table("generated_media") as batch:
        batch.drop_constraint("ck_generated_media_state", type_="check")
        batch.create_check_constraint("ck_generated_media_state", "state = 'temporary'")
