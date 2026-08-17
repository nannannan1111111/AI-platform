"""创建邮箱身份、个人账户空间、额度账户和登录会话。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_account_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建首批 SaaS 账户表。"""
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "account_spaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "credit_accounts",
        sa.Column("account_space_id", sa.String(length=36), nullable=False),
        sa.Column("available_credit_units", sa.BigInteger(), nullable=False),
        sa.Column("frozen_credit_units", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["account_space_id"], ["account_spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_space_id"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )


def downgrade() -> None:
    """按依赖逆序删除首批 SaaS 账户表。"""
    op.drop_table("auth_sessions")
    op.drop_table("credit_accounts")
    op.drop_table("account_spaces")
    op.drop_table("users")
