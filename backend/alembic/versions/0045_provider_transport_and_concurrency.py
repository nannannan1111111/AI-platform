"""Configure image transport and shared upstream account concurrency."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0045_provider_transport_and_concurrency"
down_revision: str | None = "0044_generation_queue_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add explicit response transport and upstream-account capacity settings."""
    op.add_column(
        "api_providers",
        sa.Column("image_response_mode", sa.String(length=32), nullable=False, server_default="auto"),
    )
    op.add_column(
        "api_providers",
        sa.Column("concurrency_group", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "api_providers",
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="20"),
    )
    op.add_column(
        "api_providers",
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False, server_default="600"),
    )
    op.execute("UPDATE api_providers SET concurrency_group = code WHERE concurrency_group = ''")
    with op.batch_alter_table("api_providers") as batch:
        batch.create_check_constraint(
            "ck_api_providers_max_concurrency",
            "max_concurrency BETWEEN 1 AND 1000",
        )
        batch.create_check_constraint(
            "ck_api_providers_request_timeout",
            "request_timeout_seconds BETWEEN 60 AND 1800",
        )


def downgrade() -> None:
    """Remove provider transport and capacity settings."""
    with op.batch_alter_table("api_providers") as batch:
        batch.drop_constraint("ck_api_providers_request_timeout", type_="check")
        batch.drop_constraint("ck_api_providers_max_concurrency", type_="check")
        batch.drop_column("request_timeout_seconds")
        batch.drop_column("max_concurrency")
        batch.drop_column("concurrency_group")
        batch.drop_column("image_response_mode")
