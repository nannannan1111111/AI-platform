"""Store reference image limits with image model settings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_image_model_reference_settings"
down_revision: str | None = "0054_model_reference_image_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "image_model_settings",
        sa.Column("logical_model", sa.String(length=128), nullable=False),
        sa.Column("output_spec", sa.String(length=128), nullable=False),
        sa.Column("max_reference_images", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "max_reference_images >= 0 AND max_reference_images <= 16",
            name="ck_image_model_settings_reference_limit",
        ),
        sa.PrimaryKeyConstraint("logical_model", "output_spec"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO image_model_settings (
                logical_model,
                output_spec,
                max_reference_images,
                created_at,
                updated_at
            )
            SELECT
                logical_model,
                output_spec,
                3,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM image_model_routes
            WHERE deleted_at IS NULL
            GROUP BY logical_model, output_spec
            """
        )
    )


def downgrade() -> None:
    op.drop_table("image_model_settings")
