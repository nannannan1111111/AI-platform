"""Allow a deleted model-route mapping to be created again."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_active_route_mapping_uniqueness"
down_revision: str | None = "0041_user_llm_providers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace all-history uniqueness with uniqueness among active routes."""
    with op.batch_alter_table("image_model_routes") as batch_op:
        batch_op.drop_constraint("uq_image_model_routes_mapping", type_="unique")
    op.create_index(
        "uq_active_image_model_routes_mapping",
        "image_model_routes",
        ["provider_id", "logical_model", "output_spec", "provider_model_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Restore the original all-history uniqueness."""
    op.drop_index("uq_active_image_model_routes_mapping", table_name="image_model_routes")
    with op.batch_alter_table("image_model_routes") as batch_op:
        batch_op.create_unique_constraint(
            "uq_image_model_routes_mapping",
            ["provider_id", "logical_model", "output_spec", "provider_model_name"],
        )
