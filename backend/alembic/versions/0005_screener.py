"""Stock universe, daily prices/ratios, fundamental screens.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

from syena.db import UTCDateTime

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'stocks',
        sa.Column('symbol', sa.String(32), primary_key=True),
        sa.Column('name', sa.String(200)), sa.Column('isin', sa.String(12)), sa.Column('sector', sa.String(80)),
        sa.Column('industry', sa.String(120)), sa.Column('active', sa.Boolean(), nullable=False),
        *[sa.Column(c, sa.Float()) for c in (
            'shares_outstanding', 'eps_ttm', 'book_value_ps', 'dividend_ps', 'revenue_ttm', 'net_income_ttm', 'ebitda_ttm',
            'total_debt', 'total_cash', 'roe', 'roa', 'debt_to_equity', 'current_ratio', 'sales_growth', 'profit_growth',
            'operating_margin', 'net_margin', 'insider_holding')],
        sa.Column('fundamentals_source', sa.String(16)), sa.Column('fundamentals_at', UTCDateTime(timezone=True)),
        sa.Column('fundamentals_error', sa.String(200)), sa.Column('updated_at', UTCDateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_stocks_fundamentals_at', 'stocks', ['fundamentals_at'])
    op.create_table(
        'stock_daily',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol', sa.String(32), nullable=False), sa.Column('day', sa.Date(), nullable=False),
        sa.Column('open', sa.Float()), sa.Column('high', sa.Float()), sa.Column('low', sa.Float()),
        sa.Column('close', sa.Float(), nullable=False),
        *[sa.Column(c, sa.Float()) for c in (
            'prev_close', 'volume', 'w52_high', 'w52_low', 'change_pct', 'market_cap_cr', 'pe', 'pb', 'ps', 'ev_ebitda',
            'dividend_yield', 'earnings_yield', 'pct_from_52w_high')],
        sa.UniqueConstraint('symbol', 'day', name='uq_stock_day'),
    )
    op.create_index('ix_stock_daily_day', 'stock_daily', ['day'])
    op.create_table(
        'screens',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('name', sa.String(120), nullable=False),
        sa.Column('conditions', sa.Text(), nullable=False), sa.Column('signal_text', sa.Text()),
        sa.Column('signal_rule', sa.JSON()), sa.Column('alerts', sa.Boolean(), nullable=False),
        sa.Column('algo_id', sa.Integer()), sa.Column('members', sa.JSON()),
        sa.Column('last_run_at', UTCDateTime(timezone=True)), sa.Column('created_at', UTCDateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'screen_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('screen_id', sa.Integer(), sa.ForeignKey('screens.id', ondelete='CASCADE'), nullable=False),
        sa.Column('day', sa.Date(), nullable=False), sa.Column('ran_at', UTCDateTime(timezone=True), nullable=False),
        sa.Column('members', sa.JSON(), nullable=False), sa.Column('added', sa.JSON(), nullable=False),
        sa.Column('removed', sa.JSON(), nullable=False), sa.Column('signals', sa.JSON(), nullable=False),
        sa.Column('skipped_no_data', sa.Integer(), nullable=False),
    )
    op.create_index('ix_screen_runs_screen_id', 'screen_runs', ['screen_id'])


def downgrade() -> None:
    for t in ('screen_runs', 'screens', 'stock_daily', 'stocks'):
        op.drop_table(t)
