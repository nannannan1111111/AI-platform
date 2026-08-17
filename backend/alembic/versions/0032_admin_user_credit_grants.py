"""Persist user registration time and auditable administrator credit grants."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032_admin_user_credit_grants"
down_revision: str | None = "0031_generation_task_reference_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add registration timestamps and permit administrator grant postings."""
    op.add_column("users", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    with op.batch_alter_table("credit_postings") as batch_op:
        batch_op.drop_constraint("ck_credit_posting_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_posting_kind",
            "kind IN ('recharge', 'admin_grant', 'reversal', 'freeze', 'settlement', 'release')",
        )


def downgrade() -> None:
    """Remove administrator grants only after restoring the previous posting constraint."""
    with op.batch_alter_table("credit_postings") as batch_op:
        batch_op.drop_constraint("ck_credit_posting_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_posting_kind",
            "kind IN ('recharge', 'reversal', 'freeze', 'settlement', 'release')",
        )
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("created_at")
