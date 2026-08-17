"""SQLAlchemy tables for account-owned LLM providers."""

import sqlalchemy as sa

metadata = sa.MetaData()

user_llm_providers = sa.Table(
    "user_llm_providers",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("account_space_id", sa.String(36), primary_key=True),
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
