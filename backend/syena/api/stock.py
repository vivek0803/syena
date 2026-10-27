"""One stock, everything about it: price chart (candles + averages + volume), company figures, technicals,
the AI's news verdicts, Syena's decisions and trades (shown as markers on the chart)."""
from datetime import timedelta

import pandas as pd

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.algos import indicators as ind
from syena.algos import levels as lv
from syena.algos import patterns as pat
from syena.api.common import ser
from syena.api.deps import authenticated
from syena.db import get_db, utcnow
from syena.models import Decision, NewsAnalysis, NewsItem, Trade
from syena.screener import compare

router = APIRouter(prefix="/api/stock", tags=["stock"], dependencies=[Depends(authenticated)])

RANGES = {"1D": 1, "5D": 7, "1M": 31, "3M": 92, "6M": 183, "1Y": 366, "3Y": 3 * 366, "5Y": 5 * 366}  # days shown
DEFAULT_INTERVAL = {"1D": "5minute", "5D": "15minute", "1M": "60minute", "3M": "day", "6M": "day", "1Y": "day",
                    "3Y": "day", "5Y": "day"}
# candle interval -> (label, longest range it can show: more candles than that is slow to load and to draw)
INTERVALS = {"minute": ("1m", 31), "2minute": ("2m", 31), "5minute": ("5m", 92), "15minute": ("15m", 183), "60minute": ("1h", 366),
             "240minute": ("4h", 366), "day": ("1D", 99999)}


def _num(v):
    return compare._num(v)


@router.get("/{symbol}")
def stock(symbol: str, db: Session = Depends(get_db)):
    sym = symbol.strip().upper()
    row = compare.metrics(db, [sym])[0]
    analyses = []
    for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.stage == "analysis").order_by(NewsAnalysis.id.desc()).limit(400)):
        if sym in (a.symbols or []):
            r = a.result or {}
            n = db.get(NewsItem, a.news_item_id)
            analyses.append({"id": a.id, "ts": a.created_at.isoformat(), "action": r.get("action"), "confidence": r.get("confidence"),
                             "summary": a.summary, "reasoning": r.get("reasoning"), "risks": r.get("risks") or [],
                             "event": r.get("event"), "materiality": r.get("materiality"), "materiality_basis": r.get("materiality_basis"),
                             "sentiment": a.sentiment, "model": a.model, "prompt_version": a.prompt_version,
                             "news": {"id": n.id, "title": n.title, "url": n.url, "source": n.source} if n else None})
            if len(analyses) >= 10:
                break
    decisions = [ser(d, extra={"type": d.type.value}) for d in db.scalars(
        select(Decision).where(Decision.symbol == sym).order_by(Decision.id.desc()).limit(20))]
    trades = [ser(t) for t in db.scalars(select(Trade).where(Trade.stock == sym).order_by(Trade.id.desc()).limit(50))]
    from syena.screener import shareholding

    return {"symbol": sym, "metrics": row, "analyses": analyses, "shareholding": shareholding.history(db, sym),
            "decisions": [{k: d[k] for k in ("id", "ts", "type", "side", "summary", "source", "mode", "confidence")} for d in decisions],
            "trades": [{k: t.get(k) for k in ("id", "side", "style", "mode", "status", "qty", "entry_price", "exit_price_executed",
                                               "stop_loss", "target", "profit_loss", "created_at", "closed_at", "source")} for t in trades]}


@router.get("/{symbol}/chart")
def chart(symbol: str, range: str = "1Y", interval: str | None = None, db: Session = Depends(get_db)):  # noqa: A002
    from syena.market import source

    if range not in RANGES:
        raise HTTPException(400, f"range must be one of {', '.join(RANGES)}")
    interval = interval or DEFAULT_INTERVAL[range]
    if interval not in INTERVALS:
        raise HTTPException(400, f"interval must be one of {', '.join(INTERVALS)}")
    days = RANGES[range]
    if days > INTERVALS[interval][1]:
        longest = max((k for k, d in RANGES.items() if d <= INTERVALS[interval][1]), key=lambda k: RANGES[k])
        raise HTTPException(400, f"{INTERVALS[interval][0]} candles go back at most {longest}: pick a shorter range or a bigger candle")
    sym = symbol.strip().upper()
    try:
        md = source.get_market_data(db)
    except Exception as e:
        raise HTTPException(409, f"Price data isn't connected: {e}")
    end = utcnow()
    # extra history so averages (200-day / EMA 50) and month-long pattern lists start complete
    warm = {"day": 450, "240minute": 120, "60minute": 45, "15minute": 35, "5minute": 33, "2minute": 8, "minute": 4}[interval]
    try:
        df = md.candles(sym, interval, end - timedelta(days=days + warm), end)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"Couldn't load prices: {e}")
    empty = {"symbol": sym, "range": range, "interval": interval, "candles": [], "lines": {}, "rsi": [], "patterns": [],
             "recent_patterns": [], "trendlines": [], "intervals": _interval_options(range)}
    if df is None or df.empty:
        return empty
    actions = df.attrs.get("corporate_actions", [])
    c = df["close"].astype(float)
    if interval == "day":
        lines = {"sma20": ind.sma(c, 20), "sma50": ind.sma(c, 50), "sma200": ind.sma(c, 200)}
    else:
        lines = {"ema20": ind.ema(c, 20), "ema50": ind.ema(c, 50), "vwap": ind.vwap(df)}
    rsi = ind.rsi(c, 14)
    if range == "1D":  # last trading session only
        last_day = df.index[-1].tz_convert("Asia/Kolkata").date() if df.index.tz is not None else df.index[-1].date()
        keep = pd.Series([(ts.tz_convert("Asia/Kolkata").date() if ts.tzinfo else ts.date()) == last_day for ts in df.index], index=df.index)
    else:
        keep = pd.Series(df.index >= df.index[-1] - timedelta(days=days), index=df.index)
    view = df[keep.values]
    month = int((df.index >= df.index[-1] - timedelta(days=30)).sum())
    t = lambda ts: int(ts.timestamp())  # noqa: E731
    p = lambda f: {"t": t(f["ts"]), "name": f["name"], "label": f["label"], "direction": f["direction"]}  # noqa: E731
    return {
        **empty,
        "candles": [{"t": t(ts), "o": _num(r.open), "h": _num(r.high), "l": _num(r.low), "c": _num(r.close), "v": _num(r.volume)}
                    for ts, r in view.iterrows()],
        "lines": {k: [{"t": t(ts), "v": _num(v)} for ts, v in s_[keep.values].items() if _num(v) is not None] for k, s_ in lines.items()},
        "rsi": [{"t": t(ts), "v": _num(v)} for ts, v in rsi[keep.values].items() if _num(v) is not None],
        "patterns": [p(f) for f in pat.found(df, last=len(view), chart_only=True)],
        # bullish/bearish patterns over the last month on this candle size (newest 80), for the list beside the chart;
        # indecision candles (doji, spinning top, inside/outside bar) are left out of the list
        "recent_patterns": [p(f) for f in pat.found(df, last=month) if f["direction"] != "neutral"][-80:],
        "month_summary": pat.summary(df, since=df.index[-1] - timedelta(days=30)),
        "corporate_actions": actions,
        "levels": lv.levels_payload(df, interval),
        "zone_setup": _zone_setup(df),
        "trendlines": [{"kind": sg["kind"], "slope_pct_per_bar": sg["slope_pct_per_bar"],
                        "points": [{"t": t(ts), "v": round(v, 2)} for ts, v in sg["points"]]}
                       for sg in pat.trendline_segments(view if len(view) > 20 else df, n=min(len(view), 120))],
    }


def _zone_setup(df) -> dict:
    from syena.algos import zonetrade

    try:
        return zonetrade.current_setup(df)
    except Exception:  # an explanation panel must never break the chart
        return {"available": False}


def _interval_options(range_: str) -> list[dict]:
    return [{"key": k, "label": lbl, "ok": RANGES[range_] <= mx} for k, (lbl, mx) in INTERVALS.items()]


@router.get("/{symbol}/verify")
def verify_prices(symbol: str, days: int = 5, db: Session = Depends(get_db)):
    """Syena's last few daily candles next to NSE's official end-of-day prices."""
    from syena.market import source, verify

    sym = symbol.strip().upper()
    days = max(1, min(days, 10))
    try:
        md = source.get_market_data(db)
        end = utcnow()
        daily = md.candles(sym, "day", end - timedelta(days=days * 2 + 12), end)
    except Exception as e:
        raise HTTPException(409, f"Price data isn't connected: {e}")
    return verify.verify(sym, daily, days=days)
