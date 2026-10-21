"""Market data access: LTP, index quotes and historical candles (cached + throttled).

`MarketData` is the interface the rest of the system uses. `KiteMarketData` talks to Kite;
`FrameMarketData` serves pre-loaded DataFrames (backtests, replay, tests).
Market data is for the account holder's own use only and is never redistributed.
"""
import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Protocol

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.market import kite as kite_session
from syena.models import Candle, Instrument

log = logging.getLogger("syena.data")

INDEX_KEYS = {"NIFTY": "NSE:NIFTY 50", "SENSEX": "BSE:SENSEX", "VIX": "NSE:INDIA VIX"}
# Kite historical API maximum days per request, per interval
MAX_DAYS = {"minute": 60, "3minute": 100, "5minute": 100, "10minute": 100, "15minute": 200,
            "30minute": 200, "60minute": 400, "day": 2000}
INTERVALS = list(MAX_DAYS)


class MarketData(Protocol):
    def ltp(self, symbols: list[str]) -> dict[str, float]: ...

    def index_snapshot(self) -> dict[str, dict]: ...

    def candles(self, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame: ...


class Throttle:
    """Simple rate limiter: at most `rate` calls per second across threads."""

    def __init__(self, rate: float):
        self.min_gap = 1.0 / rate
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            gap = time.monotonic() - self._last
            if gap < self.min_gap:
                time.sleep(self.min_gap - gap)
            self._last = time.monotonic()


_hist_throttle = Throttle(3.0)


def _frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df.set_index("ts")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.drop_duplicates("ts").set_index("ts").sort_index()


def cached_candles(db: Session, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows = db.execute(
        select(Candle.ts, Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume)
        .where(Candle.symbol == symbol, Candle.interval == interval, Candle.ts >= start, Candle.ts <= end)
        .order_by(Candle.ts)
    ).all()
    return _frame(rows)


def store_candles(db: Session, symbol: str, interval: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    records = [
        {"symbol": symbol, "interval": interval, "ts": ts.to_pydatetime(), "open": float(r.open),
         "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
        for ts, r in df.iterrows()
    ]
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(Candle).values(records).on_conflict_do_nothing(constraint="uq_candle")
    else:
        stmt = sqlite_insert(Candle).values(records).on_conflict_do_nothing()
    db.execute(stmt)
    db.commit()
    return len(records)


def instrument_token(db: Session, symbol: str, exchange: str = "NSE") -> int:
    tok = db.scalar(select(Instrument.instrument_token).where(
        Instrument.tradingsymbol == symbol, Instrument.exchange == exchange))
    if tok is None:
        raise KeyError(f"unknown instrument {exchange}:{symbol}; run instruments sync")
    return tok


class KiteMarketData:
    def __init__(self, db: Session):
        self.db = db

    def _kite(self):
        return kite_session.client(self.db)

    def sync_instruments(self) -> int:
        from sqlalchemy import delete, insert

        rows = self._kite().instruments("NSE")
        today = date.today()
        recs = [dict(instrument_token=r["instrument_token"], tradingsymbol=r["tradingsymbol"], name=r.get("name"),
                     exchange="NSE", segment=r.get("segment"), instrument_type=r.get("instrument_type"),
                     tick_size=r.get("tick_size") or 0.05, lot_size=r.get("lot_size") or 1, updated_on=today)
                for r in rows if r.get("segment") == "NSE" and r.get("instrument_type") == "EQ"]
        if recs:  # replace the NSE list in one go (bulk insert is far faster than row-by-row merge)
            self.db.execute(delete(Instrument).where(Instrument.exchange == "NSE"))
            self.db.execute(insert(Instrument), recs)
        self.db.commit()
        log.info("instruments synced", extra={"count": len(recs)})
        return len(recs)

    def _token(self, symbol: str) -> int:
        try:
            return instrument_token(self.db, symbol)
        except KeyError:
            self.sync_instruments()  # first use after connecting, or a newly listed stock
            try:
                return instrument_token(self.db, symbol)
            except KeyError:
                raise ValueError(f"'{symbol}' is not an NSE equity symbol. Use the trading symbol, e.g. RELIANCE, TCS, INFY.") from None

    def ltp(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        data = self._kite().ltp([f"NSE:{s}" for s in symbols])
        return {k.split(":", 1)[1]: float(v["last_price"]) for k, v in data.items()}

    def daily_quotes(self, symbols: list[str]) -> dict[str, dict]:
        out, kite = {}, self._kite()
        for i in range(0, len(symbols), 250):
            q = kite.quote([f"NSE:{s}" for s in symbols[i:i + 250]])
            for k, v in q.items():
                o = v.get("ohlc") or {}
                out[k.split(":", 1)[1]] = {"open": o.get("open"), "high": o.get("high"), "low": o.get("low"),
                                           "close": v.get("last_price"), "prev_close": o.get("close"),
                                           "volume": v.get("volume"), "w52_high": None, "w52_low": None}
        return out

    def index_snapshot(self) -> dict[str, dict]:
        q = self._kite().quote(list(INDEX_KEYS.values()))
        out = {}
        for name, key in INDEX_KEYS.items():
            v = q.get(key)
            if v:
                prev = v.get("ohlc", {}).get("close") or v["last_price"]
                out[name] = {"last": v["last_price"], "prev_close": prev,
                             "change_pct": (v["last_price"] / prev - 1) * 100 if prev else 0.0}
        return out

    def candles(self, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        if interval not in MAX_DAYS:
            raise ValueError(f"unsupported interval {interval}")
        cached = cached_candles(self.db, symbol, interval, start, end)
        need: list[tuple[datetime, datetime]] = []
        if cached.empty:
            need.append((start, end))
        else:
            first, last = cached.index[0].to_pydatetime(), cached.index[-1].to_pydatetime()
            if first - start > timedelta(days=4):  # allow weekends/holidays at the edges
                need.append((start, first))
            if end - last > timedelta(days=1) or (end > utcnow() - timedelta(days=1) and interval != "day"):
                need.append((last, end))
        if need:
            token = self._token(symbol)
            kite = self._kite()
            for a, b in need:
                cur = a
                while cur < b:
                    nxt = min(b, cur + timedelta(days=MAX_DAYS[interval]))
                    _hist_throttle.wait()
                    rows = kite.historical_data(token, cur, nxt, interval)
                    df = _frame([(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows])
                    store_candles(self.db, symbol, interval, df)
                    cur = nxt
            cached = cached_candles(self.db, symbol, interval, start, end)
        return cached


class FrameMarketData:
    """Serves DataFrames; `as_of` enforces no-lookahead (used by replay and backtests)."""

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame], indices: dict[str, dict] | None = None):
        self.frames = frames
        self.indices = indices or {}
        self.as_of: datetime | None = None

    def _cut(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.as_of is None:
            return df
        return df[df.index <= pd.Timestamp(self.as_of)]

    def candles(self, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        df = self.frames.get((symbol, interval))
        if df is None:
            return _frame([])
        if self.as_of is not None:
            end = min(end, self.as_of)
        return self._cut(df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))])

    def ltp(self, symbols: list[str]) -> dict[str, float]:
        out = {}
        for s in symbols:
            for (sym, _iv), df in self.frames.items():
                if sym == s:
                    cut = self._cut(df)
                    if not cut.empty:
                        out[s] = float(cut["close"].iloc[-1])
                    break
        return out

    def index_snapshot(self) -> dict[str, dict]:
        return self.indices
