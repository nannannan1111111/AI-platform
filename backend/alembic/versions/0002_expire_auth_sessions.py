"""为登录会话增加明确到期时间。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_expire_auth_sessions"
down_revision: str | None = "0001_account_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加到期时间，并让迁移前的未知会话立即失效。"""
    op.add_column("auth_sessions", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE auth_sessions SET expires_at = CURRENT_TIMESTAMP WHERE expires_at IS NULL"))
    with op.batch_alter_table("auth_sessions") as batch_op:
        batch_op.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    """移除登录会话到期时间。"""
    with op.batch_alter_table("auth_sessions") as batch_op:
        batch_op.drop_column("expires_at")
