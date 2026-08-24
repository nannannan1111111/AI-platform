"""Raise per-account generation concurrency ceiling to fifty."""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision = "0063_account_generation_concurrency_50"
down_revision = "0062_redeem_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    with op.batch_alter_table("account_generation_limits", recreate="always") as batch:
        batch.drop_constraint("ck_account_generation_execution_concurrency", type_="check")
        batch.create_check_constraint("ck_account_generation_execution_concurrency", "execution_concurrency BETWEEN 1 AND 50")

def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM account_generation_limits WHERE execution_concurrency > 20")).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade while account concurrency values above 20 exist")
    with op.batch_alter_table("account_generation_limits", recreate="always") as batch:
        batch.drop_constraint("ck_account_generation_execution_concurrency", type_="check")
        batch.create_check_constraint("ck_account_generation_execution_concurrency", "execution_concurrency BETWEEN 1 AND 20")
