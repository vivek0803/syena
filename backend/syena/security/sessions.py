"""Server-side sessions. The cookie holds a random token; the DB stores only its SHA-256."""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import utcnow
from syena.models import User, UserSession

COOKIE_NAME = "syena_session"
CSRF_HEADER = "X-CSRF-Token"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class NewSession:
    cookie_value: str
    csrf_token: str
    row: UserSession


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> NewSession:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    row = UserSession(
        user_id=user.id,
        token_hash=_hash(token),
        csrf_token=csrf,
        ip=ip,
        user_agent=(user_agent or "")[:255],
    )
    db.add(row)
    db.flush()
    return NewSession(cookie_value=token, csrf_token=csrf, row=row)


def load_session(db: Session, cookie_value: str | None) -> UserSession | None:
    """Return the active session for a cookie, enforcing idle and absolute timeouts."""
    if not cookie_value:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == _hash(cookie_value)))
    if row is None or row.revoked:
        return None
    s = get_settings()
    now = utcnow()
    if now - row.last_seen_at > timedelta(minutes=s.session_idle_minutes) or now - row.created_at > timedelta(
        hours=s.session_absolute_hours
    ):
        row.revoked = True
        db.commit()
        return None
    row.last_seen_at = now
    db.commit()
    return row


def revoke(db: Session, row: UserSession) -> None:
    row.revoked = True
    db.commit()


def csrf_matches(row: UserSession, header_value: str | None) -> bool:
    return bool(header_value) and secrets.compare_digest(row.csrf_token, header_value)
