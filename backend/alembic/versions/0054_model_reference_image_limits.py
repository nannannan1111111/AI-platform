"""Add per-model reference image upload limits."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_model_reference_image_limits"
down_revision: str | None = "0053_platform_content_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_price_versions") as batch_op:
        batch_op.add_column(
            sa.Column("max_reference_images", sa.Integer(), nullable=False, server_default="3")
        )
        batch_op.create_check_constraint(
            "ck_model_price_reference_image_limit",
            "max_reference_images >= 0 AND max_reference_images <= 16",
        )


def downgrade() -> None:
    with op.batch_alter_table("model_price_versions") as batch_op:
        batch_op.drop_constraint("ck_model_price_reference_image_limit", type_="check")
        batch_op.drop_column("max_reference_images")
