"""SQLAlchemy table definitions for account-owned prompt assets."""

import sqlalchemy as sa

metadata = sa.MetaData()
prompt_library_accounts = sa.Table(
    "prompt_library_accounts",
    metadata,
    sa.Column("account_space_id", sa.String(36), primary_key=True),
    sa.Column("seeded", sa.Boolean, nullable=False),
)
prompt_libraries = sa.Table(
    "prompt_libraries",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("account_space_id", sa.String(36), primary_key=True),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
)
prompt_categories = sa.Table(
    "prompt_categories",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("account_space_id", sa.String(36), primary_key=True),
    sa.Column("library_id", sa.String(36), nullable=False),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
)
prompt_items = sa.Table(
    "prompt_items",
    metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("account_space_id", sa.String(36), primary_key=True),
    sa.Column("library_id", sa.String(36), nullable=False),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("positive", sa.Text, nullable=False),
    sa.Column("negative", sa.Text, nullable=False),
    sa.Column("category_id", sa.String(64), nullable=False),
    sa.Column("scene", sa.String(500), nullable=False),
    sa.Column("params_json", sa.Text, nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
)
