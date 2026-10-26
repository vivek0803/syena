"""One-time settings change: news stays tradeable for 2 hours in market hours (was 15 minutes), and after-hours
news is kept for the next open. A value you chose yourself (anything but the old 15) is left alone.

Revision ID: 0010
Revises: 0009
"""
import json

import sqlalchemy as sa
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM app_settings WHERE key = 'news'")).first()
    if row is None:
        return
    value = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    if value.get("max_age_minutes") in (None, 15, 15.0):
        value["max_age_minutes"] = 120
        sql = "UPDATE app_settings SET value = CAST(:v AS JSON) WHERE key = 'news'" if conn.dialect.name == "postgresql" \
            else "UPDATE app_settings SET value = :v WHERE key = 'news'"
        conn.execute(sa.text(sql), {"v": json.dumps(value)})


def downgrade() -> None:
    pass
