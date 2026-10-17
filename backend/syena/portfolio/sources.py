"""Holdings sources: Zerodha (Kite equity + Coin MF), Angel One (SmartAPI), CAMS/KFintech CAS PDF.

Each sync writes a new HoldingSnapshot so history is kept and staleness is visible.
"""
import io
import logging
from datetime import datetime, time, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import utcnow
from syena.market import kite as kite_session
from syena.market.calendar import IST
from syena.models import BrokerToken, Holding, HoldingSnapshot, IsinMaster
from syena.security.crypto import decrypt, encrypt

log = logging.getLogger("syena.portfolio")

ANGEL_BASE = "https://apiconnect.angelone.in"


class SourceError(RuntimeError):
    pass


def _snapshot(db: Session, source: str, rows: list[dict], note: str | None = None) -> HoldingSnapshot:
    snap = HoldingSnapshot(source=source, note=note)
    db.add(snap)
    db.flush()
    for r in rows:
        db.add(Holding(snapshot_id=snap.id, source=source, **r))
        if r.get("isin") and r["asset_class"] == "equity":
            m = db.get(IsinMaster, r["isin"])
            if m is None:
                db.add(IsinMaster(isin=r["isin"], symbol=r.get("symbol"), name=r["name"]))
            elif r.get("symbol") and not m.symbol:
                m.symbol = r["symbol"]
            db.flush()
    db.commit()
    log.info("holdings snapshot", extra={"source": source, "count": len(rows)})
    return snap


# ---------------------------------------------------------------- Zerodha
def sync_zerodha(db: Session, kite=None) -> HoldingSnapshot:
    kite = kite or kite_session.client(db)
    rows = []
    for h in kite.holdings():
        qty = (h.get("quantity") or 0) + (h.get("t1_quantity") or 0)
        if qty <= 0:
            continue
        ltp = h.get("last_price") or h.get("close_price") or 0
        rows.append({"asset_class": "equity", "symbol": h["tradingsymbol"], "isin": h.get("isin"), "name": h["tradingsymbol"],
                     "qty": qty, "avg_price": h.get("average_price"), "ltp": ltp, "value": round(qty * ltp, 2)})
    try:
        for m in kite.mf_holdings():  # Zerodha Coin direct mutual funds
            units = m.get("quantity") or 0
            nav = m.get("last_price") or 0
            if units > 0:
                rows.append({"asset_class": "mf", "symbol": m.get("tradingsymbol"), "isin": m.get("tradingsymbol"),
                             "name": m.get("fund") or m.get("tradingsymbol"), "qty": units, "avg_price": m.get("average_price"),
                             "ltp": nav, "value": round(units * nav, 2), "folio": m.get("folio")})
    except Exception as e:
        log.info("kite mf holdings unavailable", extra={"error": str(e)})
    return _snapshot(db, "zerodha", rows)


# ---------------------------------------------------------------- Angel One (read-only)
def _angel_headers(jwt: str | None = None) -> dict:
    s = get_settings()
    if not s.angel_api_key:
        raise SourceError("SYENA_ANGEL_API_KEY is not configured")
    h = {"Content-Type": "application/json", "Accept": "application/json", "X-UserType": "USER", "X-SourceID": "WEB",
         "X-ClientLocalIP": "127.0.0.1", "X-ClientPublicIP": "127.0.0.1", "X-MACAddress": "00:00:00:00:00:00",
         "X-PrivateKey": s.angel_api_key}
    if jwt:
        h["Authorization"] = f"Bearer {jwt}"
    return h


def angel_login(db: Session, client_code: str, pin: str, totp: str, http=httpx) -> BrokerToken:
    """Credentials are used once and never stored; only the session JWT is kept (encrypted) until midnight IST."""
    r = http.post(f"{ANGEL_BASE}/rest/auth/angelbroking/user/v1/loginByPassword", headers=_angel_headers(),
                  json={"clientcode": client_code, "password": pin, "totp": totp}, timeout=20)
    body = r.json()
    if not body.get("status") or not body.get("data"):
        raise SourceError(f"Angel One login failed: {body.get('message') or r.status_code}")
    for old in db.scalars(select(BrokerToken).where(BrokerToken.broker == "angel", BrokerToken.revoked.is_(False))):
        old.revoked = True
    midnight = datetime.combine(utcnow().astimezone(IST).date() + timedelta(days=1), time(0, 0), IST)
    tok = BrokerToken(broker="angel", access_token_enc=encrypt(body["data"]["jwtToken"]), broker_user_id=client_code,
                      expires_at=midnight)
    db.add(tok)
    db.commit()
    return tok


def angel_status(db: Session) -> dict:
    t = db.scalar(select(BrokerToken).where(BrokerToken.broker == "angel", BrokerToken.revoked.is_(False)).order_by(BrokerToken.id.desc()))
    ok = t is not None and t.expires_at > utcnow()
    return {"configured": bool(get_settings().angel_api_key), "connected": ok, "expires_at": t.expires_at if ok else None,
            "client": t.broker_user_id if ok else None}


def sync_angel(db: Session, http=httpx) -> HoldingSnapshot:
    t = db.scalar(select(BrokerToken).where(BrokerToken.broker == "angel", BrokerToken.revoked.is_(False)).order_by(BrokerToken.id.desc()))
    if t is None or t.expires_at <= utcnow():
        raise SourceError("Angel One session missing or expired: connect again")
    jwt = decrypt(t.access_token_enc)
    r = http.get(f"{ANGEL_BASE}/rest/secure/angelbroking/portfolio/v1/getAllHolding", headers=_angel_headers(jwt), timeout=20)
    body = r.json()
    data = body.get("data") or {}
    items = data.get("holdings") if isinstance(data, dict) else data
    if items is None:
        r = http.get(f"{ANGEL_BASE}/rest/secure/angelbroking/portfolio/v1/getHolding", headers=_angel_headers(jwt), timeout=20)
        items = r.json().get("data") or []
    rows = []
    for h in items or []:
        qty = float(h.get("quantity") or 0) + float(h.get("t1quantity") or 0)
        if qty <= 0:
            continue
        ltp = float(h.get("ltp") or h.get("close") or 0)
        sym = (h.get("tradingsymbol") or "").replace("-EQ", "")
        rows.append({"asset_class": "equity", "symbol": sym, "isin": h.get("isin"), "name": h.get("symbolname") or sym,
                     "qty": qty, "avg_price": float(h.get("averageprice") or 0), "ltp": ltp, "value": round(qty * ltp, 2)})
    return _snapshot(db, "angel", rows)


# ---------------------------------------------------------------- CAMS / KFintech CAS
def import_cas(db: Session, pdf_bytes: bytes, password: str) -> HoldingSnapshot:
    """Parse a detailed CAS (CAMS / KFintech / MF Central) or a depository CAS (NSDL/CDSL)."""
    import casparser

    try:
        data = casparser.read_cas_pdf(io.BytesIO(pdf_bytes), password, output="dict")
    except Exception as e:
        raise SourceError(f"could not read CAS: {e}") from e
    rows = []
    for folio in data.get("folios") or []:
        for s in folio.get("schemes") or []:
            v = s.get("valuation") or {}
            units = float(s.get("close") or 0)
            if units <= 0:
                continue
            rows.append({"asset_class": "mf", "symbol": None, "isin": s.get("isin"), "name": s.get("scheme"),
                         "qty": units, "avg_price": None, "ltp": float(v.get("nav") or 0), "value": float(v.get("value") or 0),
                         "amc": folio.get("amc"), "folio": folio.get("folio"), "amfi_code": str(s.get("amfi") or "") or None})
    for acct in data.get("accounts") or []:  # NSDL/CDSL depository CAS: equities held in any demat
        for e in acct.get("equities") or []:
            qty = float(e.get("num_shares") or 0)
            if qty > 0:
                rows.append({"asset_class": "equity", "symbol": None, "isin": e.get("isin"), "name": e.get("name") or e.get("isin"),
                             "qty": qty, "avg_price": None, "ltp": float(e.get("price") or 0), "value": float(e.get("value") or 0)})
        for m in acct.get("mutual_funds") or []:
            units = float(m.get("balance") or 0)
            if units > 0:
                rows.append({"asset_class": "mf", "symbol": None, "isin": m.get("isin"), "name": m.get("name"),
                             "qty": units, "avg_price": None, "ltp": float(m.get("nav") or 0), "value": float(m.get("value") or 0)})
    period = data.get("statement_period") or {}
    if not rows:
        raise SourceError("no holdings found in this CAS")
    return _snapshot(db, "cas", rows, note=f"statement {period.get('from')} to {period.get('to')}")
