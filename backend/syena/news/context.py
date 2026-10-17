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
    return {"available": True, "close": round(last, 2), "sma50": None if sma50 != sma50 else round(float(sma50), 2),
            "sma200": None if sma200 != sma200 else round(float(sma200), 2), "rsi14": round(rsi, 2),
            "macd_hist": round(hist, 4), "score": score, "bullish": score > 0, "bearish": score < 0, "reasons": reasons}
