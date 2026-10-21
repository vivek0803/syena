"""Zerodha Kite Connect session management.

Daily login flow (works on a domain with SameSite=Strict cookies):
1. Authenticated UI calls POST /api/kite/login-url -> we mint a one-time nonce and return
   the Kite login URL with redirect_params=nonce=<n>.
2. Zerodha redirects the browser to https://<domain>/kite/callback?request_token=..&nonce=..
   (the session cookie is NOT sent on this cross-site navigation, hence the nonce).
3. We verify + burn the nonce, exchange the request token, store the access token encrypted.
Access tokens expire around 06:00 IST the next day.
"""
import hashlib
import logging
import secrets
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import utcnow
from syena.market.calendar import IST
from syena.models import BrokerToken, OAuthNonce
from syena.security.crypto import decrypt, encrypt

log = logging.getLogger("syena.kite")

NONCE_TTL = timedelta(minutes=10)


class KiteNotConnected(RuntimeError):
    pass


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _kite_cls():
    from kiteconnect import KiteConnect  # imported lazily so tests don't need network

    return KiteConnect


def login_url(db: Session) -> str:
    s = get_settings()
    if not s.kite_api_key:
        raise KiteNotConnected("SYENA_KITE_API_KEY is not configured")
    nonce = secrets.token_urlsafe(24)
    db.add(OAuthNonce(nonce_hash=_h(nonce), purpose="kite", expires_at=utcnow() + NONCE_TTL))
    db.commit()
    base = f"https://kite.zerodha.com/connect/login?v=3&api_key={s.kite_api_key}"
    return base + "&" + urlencode({"redirect_params": urlencode({"nonce": nonce})})


def consume_nonce(db: Session, nonce: str | None, purpose: str = "kite") -> bool:
    if not nonce:
        return False
    row = db.scalar(select(OAuthNonce).where(OAuthNonce.nonce_hash == _h(nonce), OAuthNonce.purpose == purpose))
    if row is None or row.used or row.expires_at < utcnow():
        return False
    row.used = True
    db.commit()
    return True


def next_expiry(now: datetime | None = None) -> datetime:
    n = (now or utcnow()).astimezone(IST)
    six = datetime.combine(n.date(), time(6, 0), IST)
    return six if n < six else six + timedelta(days=1)


def complete_login(db: Session, request_token: str) -> BrokerToken:
    s = get_settings()
    kite = _kite_cls()(api_key=s.kite_api_key)
    data = kite.generate_session(request_token, api_secret=s.kite_api_secret.get_secret_value())
    row = store_token(db, data["access_token"], data.get("user_id"))
    try:  # load the NSE symbol list now so reviews and charts work straight away
        from syena.market.data import KiteMarketData
        KiteMarketData(db).sync_instruments()
    except Exception as e:
        log.warning("instrument sync after login failed: %s", e)
    return row


def store_token(db: Session, access_token: str, user_id: str | None) -> BrokerToken:
    for old in db.scalars(select(BrokerToken).where(BrokerToken.broker == "kite", BrokerToken.revoked.is_(False))):
        old.revoked = True
    row = BrokerToken(
        broker="kite", access_token_enc=encrypt(access_token), broker_user_id=user_id, expires_at=next_expiry()
    )
    db.add(row)
    db.commit()
    log.info("kite session stored", extra={"broker_user": user_id, "expires_at": row.expires_at.isoformat()})
    return row


def active_token(db: Session) -> BrokerToken | None:
    row = db.scalar(
        select(BrokerToken)
        .where(BrokerToken.broker == "kite", BrokerToken.revoked.is_(False))
        .order_by(BrokerToken.id.desc())
    )
    if row is None or row.expires_at <= utcnow():
        return None
    return row


def token_status(db: Session) -> dict:
    row = active_token(db)
    configured = bool(get_settings().kite_api_key)
    if row is None:
        return {"configured": configured, "connected": False, "expires_at": None, "user_id": None}
    return {"configured": configured, "connected": True, "expires_at": row.expires_at, "user_id": row.broker_user_id}


def client(db: Session):
    row = active_token(db)
    if row is None:
        raise KiteNotConnected("Zerodha is not connected (or the daily token expired)")
    kite = _kite_cls()(api_key=get_settings().kite_api_key)
    kite.set_access_token(decrypt(row.access_token_enc))
    return kite


def live_precondition(db: Session) -> str | None:
    return None if active_token(db) else "Connect Zerodha first: the Kite access token is missing or expired."
