"""Option chain: listed NSE option contracts and daily open interest.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "option_contracts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("broker", sa.String(8), nullable=False),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("tradingsymbol", sa.String(64), nullable=False),
        sa.Column("underlying", sa.String(32), nullable=False, index=True),
        sa.Column("expiry", sa.Date(), nullable=False, index=True),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("option_type", sa.String(2), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(8), nullable=False),
    )
    op.create_table(
        "option_oi",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(32), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("oi", sa.Float(), nullable=False),
        sa.Column("ltp", sa.Float(), nullable=True),
        sa.UniqueConstraint("token", "day", name="uq_option_oi_token_day"),
    )


def downgrade() -> None:
    op.drop_table("option_oi")
    op.drop_table("option_contracts")
