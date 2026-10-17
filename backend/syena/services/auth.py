"""Single-user authentication: password (argon2id) + mandatory TOTP, lockout, audit."""
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import utcnow
from syena.models import LoginAudit, User
from syena.security import totp
from syena.security.passwords import verify_password
from syena.security.ratelimit import SlidingWindowLimiter

log = logging.getLogger("syena.auth")

GENERIC_LOGIN_ERROR = "Invalid credentials"

ip_limiter = SlidingWindowLimiter(limit=get_settings().login_ip_rate_per_minute, window_seconds=60)


class LoginFailed(Exception):
    """Always surfaces to the client as GENERIC_LOGIN_ERROR."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # internal only


def _audit(db: Session, username: str, success: bool, reason: str, ip: str | None, ua: str | None) -> None:
    db.add(
        LoginAudit(
            username_attempted=(username or "")[:64],
            success=success,
            reason=reason,
            ip=ip,
            user_agent=(ua or "")[:255],
        )
    )


def _register_failure(db: Session, user: User) -> None:
    s = get_settings()
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= s.login_max_failures:
        user.locked_until = utcnow() + timedelta(minutes=s.login_lockout_minutes)
        user.failed_logins = 0
        log.warning("account locked after repeated failures", extra={"username": user.username})


def is_locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > utcnow()


def verify_credentials(db: Session, user: User, password: str, totp_code: str) -> str | None:
    """Check password + TOTP for an existing user. Returns a failure reason or None.

    Counts failures toward lockout. Used for login and for LIVE re-authentication.
    """
    if is_locked(user):
        verify_password(None, password)  # keep timing uniform
        return "locked"
    if not verify_password(user.password_hash, password):
        _register_failure(db, user)
        return "bad_password"
    if not totp.verify_and_consume(user, totp_code):
        _register_failure(db, user)
        return "bad_totp"
    user.failed_logins = 0
    user.locked_until = None
    return None


def login(db: Session, username: str, password: str, totp_code: str, ip: str | None, ua: str | None) -> User:
    try:
        if not ip_limiter.allow(ip or "unknown"):
            raise LoginFailed("ip_rate_limited")
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            verify_password(None, password)
            raise LoginFailed("unknown_user")
        reason = verify_credentials(db, user, password, totp_code)
        if reason:
            raise LoginFailed(reason)
    except LoginFailed as exc:
        _audit(db, username, False, exc.reason, ip, ua)
        db.commit()
        log.warning("login failed", extra={"reason": exc.reason, "ip": ip})
        raise
    _audit(db, username, True, "ok", ip, ua)
    db.commit()
    log.info("login succeeded", extra={"ip": ip})
    return user
