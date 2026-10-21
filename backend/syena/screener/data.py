"""Stock universe, daily prices + ratios for every listed stock, and company figures (fundamentals).

- Universe: every NSE equity in your broker's instrument list (Angel One scrip master or Kite instruments).
- Daily (after close): one quote call per 50 stocks -> stock_daily rows with PE, PB, market cap... from that close.
- Fundamentals: refreshed automatically a few hundred stocks per night from Yahoo Finance (unofficial, free),
  so every stock is re-checked about once a week and new quarterly results flow in on their own.
  A Screener.in CSV export can be imported too and takes priority for a while (settings: screener.prefer_csv_days).
"""
import csv
import io
import logging
import math
import re
import time as _time
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.db import utcnow
from syena.market import calendar
from syena.models import Instrument, Stock, StockDaily

log = logging.getLogger("syena.screener")

NUMERIC_FIELDS = ["shares_outstanding", "eps_ttm", "book_value_ps", "dividend_ps", "revenue_ttm", "net_income_ttm",
                  "ebitda_ttm", "total_debt", "total_cash", "roe", "roa", "debt_to_equity", "current_ratio",
                  "sales_growth", "profit_growth", "operating_margin", "net_margin", "insider_holding"]


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


# ---------------------------------------------------------------- universe
def universe_from_md(db: Session, md) -> list[str]:
    if hasattr(md, "_token_map"):  # Angel One
        return sorted(md._token_map().keys())
    syms = [s for (s,) in db.execute(select(Instrument.tradingsymbol).where(Instrument.exchange == "NSE",
                                                                             Instrument.instrument_type == "EQ"))]
    if not syms and hasattr(md, "sync_instruments"):
        md.sync_instruments()
        syms = [s for (s,) in db.execute(select(Instrument.tradingsymbol).where(Instrument.exchange == "NSE"))]
    return sorted(set(syms))


def sync_universe(db: Session, symbols: list[str]) -> dict:
    """Add new listings, mark delisted/suspended ones inactive."""
    have = {s.symbol: s for s in db.scalars(select(Stock))}
    want = set(symbols)
    added = 0
    for sym in want - have.keys():
        db.add(Stock(symbol=sym, active=True))
        added += 1
    for sym, s in have.items():
        s.active = sym in want
    db.commit()
    return {"stocks": len(want), "added": added}


# ---------------------------------------------------------------- daily prices + ratios
def compute_ratios(s: Stock, close: float, w52_high: float | None) -> dict:
    out = {"market_cap_cr": None, "pe": None, "pb": None, "ps": None, "ev_ebitda": None, "dividend_yield": None,
           "earnings_yield": None, "pct_from_52w_high": None}
    if not close or close <= 0:
        return out
    mcap = close * s.shares_outstanding if s.shares_outstanding else None
    if mcap:
        out["market_cap_cr"] = round(mcap / 1e7, 2)
        if s.revenue_ttm and s.revenue_ttm > 0:
            out["ps"] = round(mcap / s.revenue_ttm, 2)
        if s.ebitda_ttm and s.ebitda_ttm > 0:
            out["ev_ebitda"] = round((mcap + (s.total_debt or 0) - (s.total_cash or 0)) / s.ebitda_ttm, 2)
    if s.eps_ttm:
        out["earnings_yield"] = round(s.eps_ttm / close * 100, 2)
        if s.eps_ttm > 0:
            out["pe"] = round(close / s.eps_ttm, 2)
    if s.book_value_ps and s.book_value_ps > 0:
        out["pb"] = round(close / s.book_value_ps, 2)
    if s.dividend_ps is not None:
        out["dividend_yield"] = round(s.dividend_ps / close * 100, 2)
    if w52_high and w52_high > 0:
        out["pct_from_52w_high"] = round((close / w52_high - 1) * 100, 2)
    return out


def _w52_from_history(db: Session, symbol: str, day: date, close: float) -> tuple[float | None, float | None]:
    lo, hi = db.execute(select(func.min(StockDaily.low), func.max(StockDaily.high)).where(
        StockDaily.symbol == symbol, StockDaily.day > day - timedelta(days=365))).one()
    return (max(hi, close) if hi else None), (min(lo, close) if lo else None)


def refresh_daily(db: Session, md, day: date | None = None, symbols: list[str] | None = None) -> dict:
    """Fetch today's quote for every active stock (or just `symbols`) and store prices + ratios.
    Re-running the same day updates rows."""
    day = day or calendar.to_ist(utcnow()).date()
    q = select(Stock).where(Stock.active.is_(True))
    if symbols is not None:
        q = q.where(Stock.symbol.in_(symbols))
    stocks = {s.symbol: s for s in db.scalars(q)}
    if not stocks:
        return {"updated": 0, "reason": "universe empty"}
    quotes = md.daily_quotes(sorted(stocks))
    existing = {r.symbol: r for r in db.scalars(select(StockDaily).where(StockDaily.day == day))}
    n = 0
    for sym, q in quotes.items():
        s = stocks.get(sym)
        close = _f(q.get("close"))
        if s is None or not close:
            continue
        w52h, w52l = _f(q.get("w52_high")), _f(q.get("w52_low"))
        if w52h is None:
            w52h, w52l = _w52_from_history(db, sym, day, close)
        row = existing.get(sym) or StockDaily(symbol=sym, day=day)
        prev = _f(q.get("prev_close"))
        row.open, row.high, row.low, row.close = _f(q.get("open")), _f(q.get("high")), _f(q.get("low")), close
        row.prev_close, row.volume, row.w52_high, row.w52_low = prev, _f(q.get("volume")), w52h, w52l
        row.change_pct = round((close / prev - 1) * 100, 2) if prev else None
        for k, v in compute_ratios(s, close, w52h).items():
            setattr(row, k, v)
        if sym not in existing:
            db.add(row)
        n += 1
    db.commit()
    log.info("stock daily refreshed", extra={"day": day.isoformat(), "count": n, "universe": len(stocks)})
    return {"day": day.isoformat(), "updated": n, "universe": len(stocks)}


def recompute_latest(db: Session, symbols: list[str]) -> int:
    """After new fundamentals arrive, refresh the ratios on each stock's latest daily row."""
    n = 0
    for sym in symbols:
        s = db.get(Stock, sym)
        row = db.scalar(select(StockDaily).where(StockDaily.symbol == sym).order_by(StockDaily.day.desc()).limit(1))
        if s and row:
            for k, v in compute_ratios(s, row.close, row.w52_high).items():
                setattr(row, k, v)
            n += 1
    db.commit()
    return n


# ---------------------------------------------------------------- fundamentals: Yahoo Finance (automatic)
def fetch_yahoo(symbol: str) -> dict:
    """Company figures for one NSE stock from Yahoo Finance (yfinance). Raises if Yahoo has nothing usable."""
    import yfinance as yf

    info = yf.Ticker(f"{symbol}.NS").info or {}
    if not info.get("sharesOutstanding") and info.get("trailingEps") is None:
        raise ValueError("no fundamentals on Yahoo")
    pct = lambda k: None if _f(info.get(k)) is None else round(_f(info.get(k)) * 100, 2)  # noqa: E731
    de = _f(info.get("debtToEquity"))
    return {
        "name": info.get("longName") or info.get("shortName"), "sector": info.get("sector"), "industry": info.get("industry"),
        "shares_outstanding": _f(info.get("sharesOutstanding")), "eps_ttm": _f(info.get("trailingEps")),
        "book_value_ps": _f(info.get("bookValue")), "dividend_ps": _f(info.get("dividendRate")) or 0.0,
        "revenue_ttm": _f(info.get("totalRevenue")), "net_income_ttm": _f(info.get("netIncomeToCommon")),
        "ebitda_ttm": _f(info.get("ebitda")), "total_debt": _f(info.get("totalDebt")), "total_cash": _f(info.get("totalCash")),
        "roe": pct("returnOnEquity"), "roa": pct("returnOnAssets"),
        "debt_to_equity": None if de is None else round(de / 100, 3),  # Yahoo reports it in percent
        "current_ratio": _f(info.get("currentRatio")), "sales_growth": pct("revenueGrowth"),
        "profit_growth": pct("earningsGrowth"), "operating_margin": pct("operatingMargins"),
        "net_margin": pct("profitMargins"), "insider_holding": pct("heldPercentInsiders"),
    }


def apply_fundamentals(s: Stock, data: dict, source: str) -> None:
    for k in ("name", "industry", "isin"):
        if data.get(k):
            setattr(s, k, str(data[k])[:200])
    if data.get("sector") or data.get("industry"):  # one classification: NSE's, else the source's label in NSE names
        from sqlalchemy.orm import object_session

        from syena.screener import sectors

        db = object_session(s)
        new = sectors.sector_for(db, s.symbol, data.get("sector"), data.get("industry")) if db else \
            sectors.canonical(data.get("sector"), data.get("industry"))
        s.sector = new or s.sector
    for k in NUMERIC_FIELDS:
        if k in data:
            setattr(s, k, _f(data[k]))
    s.fundamentals_source, s.fundamentals_at, s.fundamentals_error = source, utcnow(), None
    s.updated_at = utcnow()


def refresh_fundamentals(db: Session, limit: int | None = None, fetch=fetch_yahoo, pause: float = 0.4,
                         symbols: list[str] | None = None) -> dict:
    """Refresh the stalest stocks first (never-fetched, then oldest). Run nightly: the whole universe cycles weekly."""
    cfg = settings_store.get(db, "screener")
    limit = limit or int(cfg["fundamentals_per_night"])
    max_age = utcnow() - timedelta(days=float(cfg["fundamentals_max_age_days"]))
    csv_keep = utcnow() - timedelta(days=float(cfg["prefer_csv_days"]))
    if symbols:
        todo = [s for s in (db.get(Stock, x) for x in symbols) if s]
    else:
        q = (select(Stock).where(Stock.active.is_(True))
             .where((Stock.fundamentals_at.is_(None)) | (Stock.fundamentals_at < max_age))
             .order_by(Stock.fundamentals_at.is_not(None), Stock.fundamentals_at).limit(limit * 2))
        todo = [s for s in db.scalars(q) if not (s.fundamentals_source == "csv" and s.fundamentals_at and s.fundamentals_at > csv_keep)][:limit]
    ok, failed, throttled = [], 0, 0
    for s in todo:
        try:
            apply_fundamentals(s, fetch(s.symbol), "yahoo")
            ok.append(s.symbol)
            throttled = 0
        except Exception as e:
            msg = str(e)
            if re.search(r"429|too many requests|rate limit", msg, re.I):
                throttled += 1
                if throttled >= 3:  # Yahoo is rate-limiting: stop for tonight, nothing is marked as checked
                    log.warning("fundamentals paused: rate limited", extra={"done": len(ok)})
                    break
                _time.sleep(5)
                continue
            s.fundamentals_error = msg[:200]
            s.fundamentals_at = utcnow()  # retried after max_age, not every night
            failed += 1
        db.commit()
        if pause:
            _time.sleep(pause)
    recompute_latest(db, ok)
    log.info("fundamentals refreshed", extra={"ok": len(ok), "failed": failed})
    return {"refreshed": len(ok), "failed": failed, "checked": len(todo)}


# ---------------------------------------------------------------- fundamentals: Screener.in CSV export (manual)
# column header (lower-case, substring) -> field; first match wins
CSV_MAP = [
    ("nse code", "symbol"), ("nse symbol", "symbol"), ("symbol", "symbol"), ("name", "name"), ("isin", "isin"),
    ("industry", "industry"), ("sector", "sector"),
    ("eps 12m", "eps_ttm"), ("eps", "eps_ttm"), ("book value", "book_value_ps"),
    ("return on equity", "roe"), ("roe", "roe"), ("return on assets", "roa"),
    ("debt to equity", "debt_to_equity"), ("current ratio", "current_ratio"),
    ("sales growth", "sales_growth"), ("profit growth", "profit_growth"),
    ("opm", "operating_margin"), ("operating profit margin", "operating_margin"), ("npm", "net_margin"),
    ("promoter holding", "insider_holding"), ("sales", "revenue_ttm"), ("net profit", "net_income_ttm"),
    ("market capitalization", "_mcap_cr"), ("market cap", "_mcap_cr"), ("current price", "_price"), ("cmp", "_price"),
    ("dividend yield", "_div_yield"),
]
CRORE_FIELDS = {"revenue_ttm", "net_income_ttm"}


def import_screener_csv(db: Session, content: bytes) -> dict:
    text = content.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty file")
    header = [h.strip().lower() for h in rows[0]]
    cols = {}
    for i, h in enumerate(header):
        for needle, field in CSV_MAP:
            if needle in h and field not in cols.values():
                cols[i] = field
                break
    if "symbol" not in cols.values():
        raise ValueError("no 'NSE Code' column: in Screener, add the NSE Code column before exporting")
    n, unknown, done = 0, [], []
    for r in rows[1:]:
        d = {cols[i]: (r[i].strip() if i < len(r) else "") for i in cols}
        sym = (d.get("symbol") or "").upper()
        if not sym:
            continue
        s = db.get(Stock, sym)
        if s is None:
            unknown.append(sym)
            continue
        data = {k: v for k, v in d.items() if k in ("name", "isin", "industry", "sector")}
        for k, v in d.items():
            if k in NUMERIC_FIELDS:
                x = _f(re.sub(r"[,%₹\s]", "", v))
                data[k] = x * 1e7 if (x is not None and k in CRORE_FIELDS) else x
        price, mcap = _f(d.get("_price", "").replace(",", "")), _f(d.get("_mcap_cr", "").replace(",", ""))
        if price and mcap:
            data["shares_outstanding"] = mcap * 1e7 / price
        dy = _f(d.get("_div_yield", "").replace("%", ""))
        if dy is not None and price:
            data["dividend_ps"] = dy / 100 * price
        apply_fundamentals(s, data, "csv")
        done.append(sym)
        n += 1
    db.commit()
    recompute_latest(db, done)
    return {"imported": n, "not_in_universe": unknown[:20], "columns": sorted(set(cols.values()))}


def status(db: Session) -> dict:
    latest = db.scalar(select(func.max(StockDaily.day)))
    active = db.scalar(select(func.count()).select_from(Stock).where(Stock.active.is_(True))) or 0
    with_f = db.scalar(select(func.count()).select_from(Stock).where(Stock.active.is_(True), Stock.fundamentals_error.is_(None),
                                                                     Stock.fundamentals_at.is_not(None))) or 0
    priced = db.scalar(select(func.count()).select_from(StockDaily).where(StockDaily.day == latest)) if latest else 0
    return {"stocks": active, "with_fundamentals": with_f, "latest_day": latest.isoformat() if latest else None,
            "priced_latest_day": priced or 0}
