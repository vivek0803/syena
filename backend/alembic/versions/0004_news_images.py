"""News items: image URLs (Telegram posts).

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('news_items') as b:
        b.add_column(sa.Column('image_urls', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('news_items') as b:
        b.drop_column('image_urls')
