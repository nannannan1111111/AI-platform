"""持久化用户邮箱验证状态与一次性令牌。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_verify_user_emails"
down_revision: str | None = "0002_expire_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加邮箱验证状态和只保存摘要的一次性令牌表。"""
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "email_verification_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )


def downgrade() -> None:
    """移除邮箱验证状态和令牌表。"""
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified_at")
