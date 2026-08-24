"""Merge the V17 recharge-expiration head with the SaaS feature migrations."""

from collections.abc import Sequence

from alembic import op


revision = "0065_merge_v17_redeem_concurrency"
down_revision: str | Sequence[str] | None = (
    "0064_recharge_order_expiration",
    "0063_account_generation_concurrency_50",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
