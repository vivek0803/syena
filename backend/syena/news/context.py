"""Market context score from Nifty / Sensex movement and India VIX (weights configurable)."""
import pandas as pd

from syena.algos import indicators as ind


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def market_context(snapshot: dict, cfg: dict) -> dict:
    nifty = (snapshot.get("NIFTY") or {}).get("change_pct")
    sensex = (snapshot.get("SENSEX") or {}).get("change_pct")
    vix = (snapshot.get("VIX") or {}).get("change_pct")
    parts, wsum = {}, 0.0
    for name, val, w, scale, sign in (("nifty", nifty, cfg["w_nifty"], cfg["nifty_scale_pct"], 1),
                                       ("sensex", sensex, cfg["w_sensex"], cfg["nifty_scale_pct"], 1),
                                       ("vix", vix, cfg["w_vix"], cfg["vix_scale_pct"], -1)):
        if val is None:
            continue
        parts[name] = round(_clip(sign * val / scale), 3)
        wsum += w
    score = sum(parts[k] * cfg[f"w_{k}"] for k in parts) / wsum if wsum else 0.0
    return {"score": round(score, 3), "components": parts, "raw": {"nifty_pct": nifty, "sensex_pct": sensex, "vix_pct": vix,
            "vix": (snapshot.get("VIX") or {}).get("last")}, "available": bool(parts)}


def technicals(df: pd.DataFrame) -> dict:
    """Deterministic technical summary + agreement score used by the trade gate."""
    if df is None or len(df) < 30:
        return {"available": False}
    c = df["close"]
    last = float(c.iloc[-1])
    sma50 = ind.sma(c, 50).iloc[-1] if len(c) >= 50 else float("nan")
    sma200 = ind.sma(c, 200).iloc[-1] if len(c) >= 200 else float("nan")
    rsi = float(ind.rsi(c, 14).iloc[-1])
    hist = float(ind.macd(c)["hist"].iloc[-1])
    score = 0
    reasons = []
    if sma50 == sma50:
        score += 1 if last > sma50 else -1
        reasons.append(f"close {'above' if last > sma50 else 'below'} SMA50")
    if sma200 == sma200:
        score += 1 if last > sma200 else -1
        reasons.append(f"close {'above' if last > sma200 else 'below'} SMA200")
    score += 1 if hist > 0 else -1
    reasons.append(f"MACD histogram {'positive' if hist > 0 else 'negative'}")
    if rsi > 75:
        score -= 1
        reasons.append(f"RSI {rsi:.0f} overbought")
    elif rsi < 25:
        score -= 1
        reasons.append(f"RSI {rsi:.0f} oversold (falling knife)")
    # candlestick patterns on the last 5 daily candles and where price sits against its trendlines
    from syena.algos import patterns as pat

    candles = [f"{f['label']} ({f['direction']}, {f['ts'].date():%d %b})" for f in pat.found(df, last=5, chart_only=True)]
    if candles:
        reasons.append("candles: " + "; ".join(candles))
    lines = {}
    for kind in ("support", "resistance"):
        v = pat.trendline(df.iloc[-120:], 60, kind).iloc[-1]
        if v == v:
            lines[kind] = round(float(v), 2)
    if "resistance" in lines and last > lines["resistance"]:
        reasons.append(f"above trendline resistance {lines['resistance']} (breakout)")
    if "support" in lines and last < lines["support"]:
        reasons.append(f"below trendline support {lines['support']} (breakdown)")
    return {"available": True, "close": round(last, 2), "sma50": None if sma50 != sma50 else round(float(sma50), 2),
            "sma200": None if sma200 != sma200 else round(float(sma200), 2), "rsi14": round(rsi, 2),
            "macd_hist": round(hist, 4), "score": score, "bullish": score > 0, "bearish": score < 0, "reasons": reasons,
            "candle_patterns": candles, "trendlines": lines}


TF_LABELS = {"minute": "1-minute", "3minute": "3-minute", "5minute": "5-minute", "15minute": "15-minute",
             "30minute": "30-minute", "60minute": "1-hour", "day": "daily"}


def multi_timeframe(md, symbol: str, as_of, timeframes: list[str], days: int = 30) -> list[dict]:
    """Candlestick patterns and trendline position over the last `days` on each timeframe (TradingView-style
    multi-timeframe read). A timeframe whose candles can't be loaded is reported as unavailable, never fatal."""
    from datetime import timedelta

    from syena.algos import patterns as pat

    out = []
    for tf in timeframes:
        try:
            warm = 40 if tf == "day" else 3  # a little extra history for the trend checks before the window
            df = md.candles(symbol, tf, as_of - timedelta(days=days + warm), as_of)
            df = df[df.index <= as_of] if len(df) else df
            s = pat.summary(df, since=as_of - timedelta(days=days))
        except Exception as e:
            s = {"available": False, "error": str(e)[:120]}
        out.append({"timeframe": TF_LABELS.get(tf, tf), **s})
    return out
