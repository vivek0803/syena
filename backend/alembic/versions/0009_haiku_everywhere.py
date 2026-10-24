"""One-time settings change: Claude Haiku 5.5 for every AI job, and one news analysis per stock per hour.

Done once here (not on every read) so a model you pick later in Settings stays picked.

Revision ID: 0009
Revises: 0008
"""
import json

import sqlalchemy as sa
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

HAIKU = "claude-haiku-5-5"


def _update(conn, key, fn):
    row = conn.execute(sa.text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}).first()
    if row is None:
        return  # never saved: the new defaults already apply
    value = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    if fn(value):
        sql = "UPDATE app_settings SET value = CAST(:v AS JSON) WHERE key = :k" if conn.dialect.name == "postgresql" \
            else "UPDATE app_settings SET value = :v WHERE key = :k"
        conn.execute(sa.text(sql), {"v": json.dumps(value), "k": key})


def upgrade() -> None:
    conn = op.get_bind()

    def models(v):
        changed = False
        for tier in ("triage", "analysis", "review", "judge"):
            if v.get(tier) and v[tier] != HAIKU:
                v[tier] = HAIKU
                changed = True
        return changed

    def news(v):
        if v.get("symbol_dedup_minutes") == 30:
            v["symbol_dedup_minutes"] = 60
            return True
        return False

    _update(conn, "models", models)
    _update(conn, "news", news)


def downgrade() -> None:
    pass
