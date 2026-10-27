"""Screener: free cash flow, 3-year averages and FII/DII shareholding; news reads more items per run.

Revision ID: 0011
Revises: 0010
"""
import json

import sqlalchemy as sa
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

STOCK_COLS = ["operating_cash_flow", "capex", "free_cash_flow", "fcf_3y_avg", "sales_cagr_3y", "profit_cagr_3y", "eps_cagr_3y",
              "roe_3y_avg", "roce_3y_avg", "opm_3y_avg", "promoter_holding", "fii_holding", "dii_holding", "mf_holding",
              "public_holding", "retail_holding", "promoter_change", "fii_change", "dii_change"]


def upgrade() -> None:
    for c in STOCK_COLS:
        op.add_column("stocks", sa.Column(c, sa.Float(), nullable=True))
    op.add_column("stocks", sa.Column("shareholding_date", sa.Date(), nullable=True))
    op.add_column("stock_daily", sa.Column("fcf_yield", sa.Float(), nullable=True))
    op.create_table(
        "shareholding_patterns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        *[sa.Column(c, sa.Float(), nullable=True) for c in ("promoter", "fii", "dii", "mf", "insurance", "public", "retail")],
        sa.Column("shareholders", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(300), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("symbol", "as_of", name="uq_shp_symbol_date"),
    )
    # existing stocks: fetch the new figures soon instead of waiting up to a week
    op.execute("UPDATE stocks SET fundamentals_at = NULL WHERE fundamentals_source = 'yahoo'")
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM app_settings WHERE key = 'news'")).first()
    if row is not None:
        value = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        if value.get("max_items_per_run") in (None, 60, 60.0):
            value["max_items_per_run"] = 100
            sql = "UPDATE app_settings SET value = CAST(:v AS JSON) WHERE key = 'news'" if conn.dialect.name == "postgresql" \
                else "UPDATE app_settings SET value = :v WHERE key = 'news'"
            conn.execute(sa.text(sql), {"v": json.dumps(value)})


def downgrade() -> None:
    op.drop_table("shareholding_patterns")
    op.drop_column("stock_daily", "fcf_yield")
    op.drop_column("stocks", "shareholding_date")
    for c in STOCK_COLS:
        op.drop_column("stocks", c)
