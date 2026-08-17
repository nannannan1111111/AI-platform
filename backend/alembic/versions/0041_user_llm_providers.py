"""Persist account-owned LLM providers separately from platform image routing."""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0041_user_llm_providers"
down_revision: str | None = "0040_prompt_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_llm_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_space_id", sa.String(36), sa.ForeignKey("account_spaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("models_json", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("secret_ref", sa.String(255), nullable=False),
        sa.Column("key_fingerprint", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_space_id", "code", name="uq_user_llm_provider_account_code"),
    )
    op.create_index("ix_user_llm_providers_account", "user_llm_providers", ["account_space_id"])


def downgrade() -> None:
    op.drop_index("ix_user_llm_providers_account", table_name="user_llm_providers")
    op.drop_table("user_llm_providers")
