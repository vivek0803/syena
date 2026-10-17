"""Setup features captured at decision time. Lessons trigger on these; reviews and mining group by them."""
from datetime import datetime

import pandas as pd

from syena.algos import indicators as ind
from syena.market.calendar import to_ist


def time_bucket(ts: datetime) -> str:
    t = to_ist(ts).time()
    if t.hour < 10 or (t.hour == 10 and t.minute < 30):
        return "open"
    if t.hour >= 14:
        return "close"
    return "mid"


def vix_regime(vix: float | None) -> str | None:
    if vix is None:
        return None
    return "low" if vix < 13 else "high" if vix > 20 else "normal"


def technical_features(df: pd.DataFrame | None) -> dict:
    if df is None or len(df) < 30:
        return {}
    c = df["close"]
    out = {
        "rsi": float(ind.rsi(c, 14).iloc[-1]),
        "adx": float(ind.adx(df, 14).iloc[-1]),
        "atr_pct": float(ind.atr(df, 14).iloc[-1] / c.iloc[-1] * 100),
        "above_sma50": bool(len(df) >= 50 and c.iloc[-1] > ind.sma(c, 50).iloc[-1]),
        "ret_5": float(c.iloc[-1] / c.iloc[-6] * 100 - 100) if len(df) > 6 else None,
        "vol_ratio": float(df["volume"].iloc[-1] / ind.volume_sma(df, 20).iloc[-1]) if df["volume"].iloc[-20:].sum() else None,
    }
    return {k: (round(v, 3) if isinstance(v, float) and v == v else v) for k, v in out.items() if v is None or v == v}


def build_setup(*, symbol: str, style: str, source: str, ts: datetime, df: pd.DataFrame | None = None,
                event_type: str | None = None, vix: float | None = None, market_context: float | None = None,
                algo_id: int | None = None, sector: str | None = None) -> dict:
    s = {
        "symbol": symbol, "style": style, "source": source, "time_of_day": time_bucket(ts),
        "weekday": to_ist(ts).strftime("%a").lower(), "event_type": event_type, "vix_regime": vix_regime(vix),
        "market_context": None if market_context is None else round(market_context, 3), "algo_id": algo_id,
        "sector": sector,
    }
    s.update(technical_features(df))
    return {k: v for k, v in s.items() if v is not None}


def describe_setup(s: dict) -> str:
    """Text form used for semantic retrieval of lessons and similar trades."""
    parts = [f"{s.get('source', '')} {s.get('style', '')} trade in {s.get('symbol', '')}"]
    for k in ("event_type", "sector", "time_of_day", "vix_regime"):
        if s.get(k):
            parts.append(f"{k.replace('_', ' ')} {s[k]}")
    for k in ("rsi", "adx", "atr_pct", "market_context"):
        if k in s:
            parts.append(f"{k} {s[k]}")
    if "above_sma50" in s:
        parts.append("above sma50" if s["above_sma50"] else "below sma50")
    return ", ".join(parts)
