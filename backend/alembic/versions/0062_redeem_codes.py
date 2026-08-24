"""One-time balance redemption codes."""
import sqlalchemy as sa
from alembic import op
revision = "0062_redeem_codes"
down_revision = "0061_password_reset_tokens"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("redeem_codes",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("code_hint", sa.String(16), nullable=False), sa.Column("credit_units", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_by", sa.String(255)),
        sa.Column("redeemed_at", sa.DateTime(timezone=True)), sa.Column("redeemed_by", sa.String(36)))
def downgrade(): op.drop_table("redeem_codes")
