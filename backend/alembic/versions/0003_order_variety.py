"""Order variety (regular | amo) for after-market orders.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('orders') as b:
        b.add_column(sa.Column('variety', sa.String(length=8), nullable=False, server_default='regular'))


def downgrade() -> None:
    with op.batch_alter_table('orders') as b:
        b.drop_column('variety')
