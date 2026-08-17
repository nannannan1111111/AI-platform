"""Persist administrator-owned API sources and image model routes."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_model_routing"
down_revision: str | None = "0016_global_storage_allowance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create API source metadata and compatible image model routes."""
    op.create_table(
        "api_providers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("protocol", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("secret_ref", sa.String(length=1024), nullable=False),
        sa.Column("key_fingerprint", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "image_model_routes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("logical_model", sa.String(length=128), nullable=False),
        sa.Column("output_spec", sa.String(length=128), nullable=False),
        sa.Column("provider_model_name", sa.String(length=255), nullable=False),
        sa.Column("compatibility_group", sa.String(length=128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("health_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["api_providers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_id",
            "logical_model",
            "output_spec",
            "provider_model_name",
            name="uq_image_model_routes_mapping",
        ),
    )


def downgrade() -> None:
    """Remove model routes before their API sources."""
    op.drop_table("image_model_routes")
    op.drop_table("api_providers")
