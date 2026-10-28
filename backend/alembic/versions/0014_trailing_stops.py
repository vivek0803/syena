"""Trailing stop-loss state on trades; PEG ratio on the daily stock rows.

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("trail", sa.JSON(), nullable=True))
    op.add_column("stock_daily", sa.Column("peg", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("stock_daily", "peg")
    op.drop_column("trades", "trail")
