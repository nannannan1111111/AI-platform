"""Persist account-owned prompt asset libraries and templates."""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0040_prompt_assets"
down_revision: str | None = "0039_backfill_canvas_reference_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table("prompt_library_accounts", sa.Column("account_space_id", sa.String(36), sa.ForeignKey("account_spaces.id", ondelete="CASCADE"), primary_key=True), sa.Column("seeded", sa.Boolean, nullable=False))
    op.create_table("prompt_libraries", sa.Column("id", sa.String(36), primary_key=True), sa.Column("account_space_id", sa.String(36), sa.ForeignKey("account_spaces.id", ondelete="CASCADE"), primary_key=True), sa.Column("name", sa.String(120), nullable=False), sa.Column("position", sa.Integer, nullable=False))
    op.create_table("prompt_categories", sa.Column("id", sa.String(36), primary_key=True), sa.Column("account_space_id", sa.String(36), sa.ForeignKey("account_spaces.id", ondelete="CASCADE"), primary_key=True), sa.Column("library_id", sa.String(36), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("position", sa.Integer, nullable=False), sa.ForeignKeyConstraint(["library_id", "account_space_id"], ["prompt_libraries.id", "prompt_libraries.account_space_id"], ondelete="CASCADE"))
    op.create_table("prompt_items", sa.Column("id", sa.String(64), primary_key=True), sa.Column("account_space_id", sa.String(36), sa.ForeignKey("account_spaces.id", ondelete="CASCADE"), primary_key=True), sa.Column("library_id", sa.String(36), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("positive", sa.Text, nullable=False), sa.Column("negative", sa.Text, nullable=False), sa.Column("category_id", sa.String(64), nullable=False), sa.Column("scene", sa.String(500), nullable=False), sa.Column("params_json", sa.Text, nullable=False), sa.Column("position", sa.Integer, nullable=False), sa.ForeignKeyConstraint(["library_id", "account_space_id"], ["prompt_libraries.id", "prompt_libraries.account_space_id"], ondelete="CASCADE"))
    op.create_index("ix_prompt_items_account_library", "prompt_items", ["account_space_id", "library_id"])

def downgrade() -> None:
    op.drop_index("ix_prompt_items_account_library", table_name="prompt_items")
    op.drop_table("prompt_items"); op.drop_table("prompt_categories"); op.drop_table("prompt_libraries"); op.drop_table("prompt_library_accounts")

