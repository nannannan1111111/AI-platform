"""Persist retryable, irreversible personal asset removals."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_remove_personal_assets"
down_revision: str | None = "0014_personal_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add retryable removal states and the first removal time."""
    with op.batch_alter_table("personal_assets") as batch:
        batch.drop_constraint("ck_personal_assets_state", type_="check")
        batch.add_column(sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_personal_assets_state",
            "state IN ('pending', 'active', 'removing', 'removed')",
        )


def downgrade() -> None:
    """Remove tombstones that the previous schema cannot represent."""
    op.execute(sa.text("DELETE FROM personal_assets WHERE state IN ('removing', 'removed')"))
    with op.batch_alter_table("personal_assets") as batch:
        batch.drop_constraint("ck_personal_assets_state", type_="check")
        batch.create_check_constraint(
            "ck_personal_assets_state",
            "state IN ('pending', 'active')",
        )
        batch.drop_column("removed_at")
