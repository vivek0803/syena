"""Angel One SmartAPI market data: free live prices (LTP/OHLC) and historical candles.

Used when SYENA_MARKET_DATA=angel. Syena logs in by itself with your client ID, PIN and the
TOTP secret from smartapi.angelbroking.com (all from deploy/syena-local.conf); only the session
JWT is stored, encrypted, and refreshed automatically. Orders still go through Zerodha.
"""
import logging
import time as _time
from datetime import date, datetime, time, timedelta

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import utcnow
from syena.market.calendar import IST
from syena.market.data import Throttle, _frame, adjust_corporate_actions, cached_candles, missing_ranges, store_candles
from syena.models import AppSetting, BrokerToken
from syena.security.crypto import decrypt, encrypt

log = logging.getLogger("syena.angel")

BASE = "https://apiconnect.angelone.in"
SCRIP_MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
TOKENS_KEY = "angel_tokens"  # app_settings row: {"date": "YYYY-MM-DD", "map": {"RELIANCE": "2885", ...}}

# Syena interval name -> (Angel interval, max days per request)
INTERVALS = {"minute": ("ONE_MINUTE", 30), "3minute": ("THREE_MINUTE", 60), "5minute": ("FIVE_MINUTE", 100),
             "10minute": ("TEN_MINUTE", 100), "15minute": ("FIFTEEN_MINUTE", 200), "30minute": ("THIRTY_MINUTE", 200),
             "60minute": ("ONE_HOUR", 400), "day": ("ONE_DAY", 2000)}
INDEX_TOKENS = {"NIFTY": ("NSE", "99926000"), "VIX": ("NSE", "99926017"), "SENSEX": ("BSE", "99919000")}
TOKEN_ERRORS = {"AG8001", "AG8002", "AG8003", "AB1010", "AB8050", "AB8051"}

# Angel allows about 3 history requests a second; the web app and the worker each keep their own limiter,
# so each stays well under half of that. A 403 still means "slow down": wait and retry, never log in again.
_hist_throttle = Throttle(1.4)
_quote_throttle = Throttle(5.0)
BACKOFF = (1.5, 3.0, 6.0)
LOGIN_BLOCK_KEY = "_angel_login_block"  # app_settings row: {"until": iso time} after Angel refused a login
LOGIN_BLOCK_SECONDS = 90
_sleep = _time.sleep


def _rate_limited(status: int, body: dict) -> bool:
    msg = str(body.get("message") or "").lower()
    return (status in (403, 429) and body.get("errorcode") not in TOKEN_ERRORS) or "access rate" in msg or "too many" in msg


def _login_blocked_until(db: Session) -> datetime | None:
    row = db.get(AppSetting, LOGIN_BLOCK_KEY)
    until = (row.value or {}).get("until") if row and isinstance(row.value, dict) else None
    if not until:
        return None
    t = datetime.fromisoformat(until)
    return t if t > utcnow() else None


def _block_logins(db: Session) -> None:
    val = {"until": (utcnow() + timedelta(seconds=LOGIN_BLOCK_SECONDS)).isoformat()}
    row = db.get(AppSetting, LOGIN_BLOCK_KEY)
    if row is None:
        db.add(AppSetting(key=LOGIN_BLOCK_KEY, value=val, updated_at=utcnow()))
    else:
        row.value, row.updated_at = val, utcnow()
    db.commit()


class AngelError(RuntimeError):
    pass


def configured() -> bool:
    s = get_settings()
    return bool(s.angel_api_key and s.angel_client_id and s.angel_pin.get_secret_value()
                and s.angel_totp_secret.get_secret_value())


def missing_settings() -> list[str]:
    s = get_settings()
    names = {"SYENA_ANGEL_API_KEY": s.angel_api_key, "SYENA_ANGEL_CLIENT_ID": s.angel_client_id,
             "SYENA_ANGEL_PIN": s.angel_pin.get_secret_value(), "SYENA_ANGEL_TOTP_SECRET": s.angel_totp_secret.get_secret_value()}
    return [k for k, v in names.items() if not v]


def _headers(jwt: str | None = None) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json", "X-UserType": "USER", "X-SourceID": "WEB",
         "X-ClientLocalIP": "127.0.0.1", "X-ClientPublicIP": "127.0.0.1", "X-MACAddress": "00:00:00:00:00:00",
         "X-PrivateKey": get_settings().angel_api_key}
    if jwt:
        h["Authorization"] = f"Bearer {jwt}"
    return h


def _active(db: Session) -> BrokerToken | None:
    t = db.scalar(select(BrokerToken).where(BrokerToken.broker == "angel", BrokerToken.revoked.is_(False))
                  .order_by(BrokerToken.id.desc()))
    return t if t is not None and t.expires_at > utcnow() else None


def login(db: Session, http=httpx) -> str:
    """Log in with PIN + current TOTP code; stores the JWT (encrypted) until midnight IST."""
    import pyotp

    if not configured():
        raise AngelError("Angel One is not set up. Fill in " + ", ".join(missing_settings()) + " in deploy/syena-local.conf")
    if _login_blocked_until(db):  # shared by the web app and the worker, so neither keeps knocking
        raise AngelError("Angel One is limiting requests right now (too many in a short time). Syena will try again in about a minute.")
    s = get_settings()
    secret = s.angel_totp_secret.get_secret_value().replace(" ", "").upper()
    try:
        code = pyotp.TOTP(secret).now()
    except Exception:
        raise AngelError("SYENA_ANGEL_TOTP_SECRET is not a valid TOTP secret (copy the text code shown under the QR)") from None
    r = http.post(f"{BASE}/rest/auth/angelbroking/user/v1/loginByPassword", headers=_headers(), timeout=20,
                  json={"clientcode": s.angel_client_id, "password": s.angel_pin.get_secret_value(), "totp": code})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if _rate_limited(r.status_code, body):
        _block_logins(db)
        log.warning("angel login rate limited", extra={"status": r.status_code})
        raise AngelError("Angel One is limiting requests right now (too many in a short time). Syena will try again in about a minute.")
    if not body:
        raise AngelError(f"Angel One login failed (HTTP {r.status_code})")
    if not body.get("status") or not body.get("data"):
        raise AngelError(f"Angel One login failed: {body.get('message') or r.status_code} {body.get('errorcode') or ''}".strip())
    jwt = body["data"]["jwtToken"]
    for old in db.scalars(select(BrokerToken).where(BrokerToken.broker == "angel", BrokerToken.revoked.is_(False))):
        old.revoked = True
    midnight = datetime.combine(utcnow().astimezone(IST).date() + timedelta(days=1), time(0, 0), IST)
    db.add(BrokerToken(broker="angel", access_token_enc=encrypt(jwt), broker_user_id=s.angel_client_id,
                       expires_at=midnight - timedelta(minutes=5)))
    db.commit()
    log.info("angel one session started")
    return jwt


class AngelMarketData:
    """Same interface as KiteMarketData (ltp / index_snapshot / candles)."""

    def __init__(self, db: Session, http=httpx):
        self.db = db
        self.http = http

    # ------------------------------------------------------------ session + calls
    def _jwt(self, fresh: bool = False) -> str:
        t = None if fresh else _active(self.db)
        return decrypt(t.access_token_enc) if t else login(self.db, self.http)

    def _post(self, path: str, payload: dict) -> dict:
        relogged, waits = False, list(BACKOFF)
        fresh = False
        while True:
            r = self.http.post(f"{BASE}{path}", headers=_headers(self._jwt(fresh=fresh)), json=payload, timeout=20)
            fresh = False
            try:
                body = r.json()
            except ValueError:
                body = {"status": False, "message": f"HTTP {r.status_code}"}
            if body.get("status"):
                return body.get("data") or {}
            if body.get("errorcode") in TOKEN_ERRORS or r.status_code == 401:  # session really expired
                if not relogged:
                    relogged = fresh = True
                    continue
            elif _rate_limited(r.status_code, body):  # too fast: wait and retry with the same session
                if waits:
                    _sleep(waits.pop(0))
                    continue
                raise AngelError("Angel One: too many requests, still limited after retrying. Try again in a minute.")
            raise AngelError(f"Angel One: {body.get('message') or 'request failed'} {body.get('errorcode') or ''}".strip())

    # ------------------------------------------------------------ symbols
    def _token_map(self, refresh: bool = False) -> dict[str, str]:
        row = self.db.get(AppSetting, TOKENS_KEY)
        if row and not refresh and isinstance(row.value, dict) and row.value.get("map"):
            return row.value["map"]
        return self.sync_instruments()

    def sync_instruments(self) -> dict[str, str]:
        r = self.http.get(SCRIP_MASTER, timeout=120)
        r.raise_for_status()
        mp, be = {}, {}
        for it in r.json():
            sym = it.get("symbol") or ""
            if it.get("exch_seg") == "NSE" and sym.endswith("-EQ"):
                mp[sym[:-3]] = str(it.get("token"))
            elif it.get("exch_seg") == "NSE" and sym.endswith("-BE"):  # trade-to-trade stocks have no -EQ line
                be[sym[:-3]] = str(it.get("token"))
        for k, v in be.items():
            mp.setdefault(k, v)
        if not mp:
            raise AngelError("Angel One symbol list came back empty")
        row = self.db.get(AppSetting, TOKENS_KEY) or AppSetting(key=TOKENS_KEY)
        row.value, row.updated_at = {"date": date.today().isoformat(), "map": mp}, utcnow()
        self.db.merge(row)
        self.db.commit()
        log.info("angel instruments synced", extra={"count": len(mp)})
        return mp

    def _tokens(self, symbols: list[str]) -> dict[str, str]:
        mp = self._token_map()
        if any(s not in mp for s in symbols):
            row = self.db.get(AppSetting, TOKENS_KEY)
            if not row or (row.value or {}).get("date") != date.today().isoformat():
                mp = self._token_map(refresh=True)  # newly listed stock: refresh at most once a day
        return {s: mp[s] for s in symbols if s in mp}

    def token(self, symbol: str) -> str:
        t = self._tokens([symbol]).get(symbol)
        if t is None:
            raise ValueError(f"'{symbol}' is not an NSE equity symbol. Use the trading symbol, e.g. RELIANCE, TCS, INFY.")
        return t

    # ------------------------------------------------------------ prices
    def _quote(self, mode: str, exchange_tokens: dict[str, list[str]]) -> list[dict]:
        out = []
        for exch, toks in exchange_tokens.items():
            for i in range(0, len(toks), 50):
                _quote_throttle.wait()
                data = self._post("/rest/secure/angelbroking/market/v1/quote/",
                                  {"mode": mode, "exchangeTokens": {exch: toks[i:i + 50]}})
                out.extend(data.get("fetched") or [])
        return out

    def ltp(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        toks = self._tokens(symbols)
        if not toks:
            return {}
        by_token = {v: k for k, v in toks.items()}
        out = {}
        for q in self._quote("LTP", {"NSE": list(toks.values())}):
            sym = by_token.get(str(q.get("symbolToken")))
            if sym and q.get("ltp") is not None:
                out[sym] = float(q["ltp"])
        return out

    def daily_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Full quote (OHLC, volume, 52-week range) for many stocks: one request per 50 symbols."""
        toks = self._tokens(symbols)
        by_token = {v: k for k, v in toks.items()}
        out = {}
        for q in self._quote("FULL", {"NSE": list(toks.values())}):
            sym = by_token.get(str(q.get("symbolToken")))
            if not sym or q.get("ltp") is None:
                continue
            out[sym] = {"open": q.get("open"), "high": q.get("high"), "low": q.get("low"), "close": q.get("ltp"),
                        "prev_close": q.get("close"), "volume": q.get("tradeVolume"),
                        "w52_high": q.get("52WeekHigh"), "w52_low": q.get("52WeekLow")}
        return out

    def index_snapshot(self) -> dict[str, dict]:
        req: dict[str, list[str]] = {}
        for exch, tok in INDEX_TOKENS.values():
            req.setdefault(exch, []).append(tok)
        names = {tok: name for name, (_e, tok) in INDEX_TOKENS.items()}
        out = {}
        for q in self._quote("OHLC", req):
            name = names.get(str(q.get("symbolToken")))
            if not name or q.get("ltp") is None:
                continue
            last = float(q["ltp"])
            prev = float(q.get("close") or last)
            out[name] = {"last": last, "prev_close": prev, "change_pct": (last / prev - 1) * 100 if prev else 0.0}
        return out

    def candles(self, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        if interval not in INTERVALS:
            raise ValueError(f"unsupported interval {interval}")
        cached = cached_candles(self.db, symbol, interval, start, end)
        need = missing_ranges(cached, symbol, interval, start, end)
        if need:
            tok = self.token(symbol)
            name, max_days = INTERVALS[interval]
            for a, b in need:
                cur = a
                while cur < b:
                    nxt = min(b, cur + timedelta(days=max_days))
                    _hist_throttle.wait()
                    rows = self._post("/rest/secure/angelbroking/historical/v1/getCandleData", {
                        "exchange": "NSE", "symboltoken": tok, "interval": name,
                        "fromdate": cur.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                        "todate": nxt.astimezone(IST).strftime("%Y-%m-%d %H:%M")}) or []
                    store_candles(self.db, symbol, interval, _frame([tuple(r[:6]) for r in rows]))
                    cur = nxt
            cached = cached_candles(self.db, symbol, interval, start, end)
        return adjust_corporate_actions(cached)
