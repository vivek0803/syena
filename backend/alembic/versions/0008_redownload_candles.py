"""Clear the saved candles once so they download again correctly.

Earlier versions saved a candle that was still forming (opened mid-session) and never overwrote it, so some
days kept a wrong high/low/close. Saving now overwrites; this clears the old copies. It is only a cache:
charts, screens and backtests download what they need again from the broker.

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM candles_cache")


def downgrade() -> None:
    pass
