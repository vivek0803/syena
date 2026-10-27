"""Paper option trades: trades carry the instrument (shares or option) and the option's details; algos can trade options.

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("instrument", sa.String(4), nullable=False, server_default="EQ"))
    op.add_column("trades", sa.Column("option", sa.JSON(), nullable=True))
    op.add_column("algos", sa.Column("instrument", sa.String(8), nullable=False, server_default="EQ"))


def downgrade() -> None:
    op.drop_column("algos", "instrument")
    op.drop_column("trades", "option")
    op.drop_column("trades", "instrument")
