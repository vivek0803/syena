"""Watchlists for the Compare page.

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

from syena.db import UTCDateTime

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'watchlists',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('symbols', sa.JSON(), nullable=False),
        sa.Column('ai_result', sa.JSON(), nullable=True),
        sa.Column('ai_at', UTCDateTime(), nullable=True),
        sa.Column('created_at', UTCDateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('watchlists')
