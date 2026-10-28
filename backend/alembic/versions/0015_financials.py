"""Company financial statements (quarterly results, annual P&L, balance sheet, cash flow) for company pages.

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("kind", sa.String(2), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("symbol", "kind", "period_end", name="uq_financials_period"),
    )
    # fetch statements for every stock soon instead of waiting up to a week
    op.execute("UPDATE stocks SET fundamentals_at = NULL WHERE fundamentals_source = 'yahoo'")


def downgrade() -> None:
    op.drop_table("financials")
