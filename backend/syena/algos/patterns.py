"""Candlestick patterns and trendlines, computed only from bars up to each point (no look-ahead).

Patterns return a boolean Series: True on the bar where the pattern completes. Reversal patterns that need a
prior move (hammer, hanging man, engulfing, stars...) check the trend over the previous `TREND` bars, the way
traders read them: a hammer after a fall, a hanging man after a rise.

Trendlines join the two most recent confirmed swing lows (support) or swing highs (resistance) inside the
lookback window and extend that line to the current bar. A swing point is only "confirmed" `PIVOT` bars after
it happens, exactly as it would be seen live.
"""
import numpy as np
import pandas as pd

TREND = 5  # bars used to judge the move before a reversal pattern
PIVOT = 3  # bars on each side that make a swing high/low

# name -> (label, direction: bullish | bearish | neutral, show on chart by default)
PATTERNS: dict[str, tuple[str, str, bool]] = {
    "hammer": ("Hammer", "bullish", True),
    "inverted_hammer": ("Inverted hammer", "bullish", True),
    "bullish_engulfing": ("Bullish engulfing", "bullish", True),
    "piercing_line": ("Piercing line", "bullish", True),
    "morning_star": ("Morning star", "bullish", True),
    "three_white_soldiers": ("Three white soldiers", "bullish", True),
    "bullish_harami": ("Bullish harami", "bullish", True),
    "tweezer_bottom": ("Tweezer bottom", "bullish", True),
    "dragonfly_doji": ("Dragonfly doji", "bullish", False),
    "bullish_marubozu": ("Bullish marubozu", "bullish", False),
    "shooting_star": ("Shooting star", "bearish", True),
    "hanging_man": ("Hanging man", "bearish", True),
    "bearish_engulfing": ("Bearish engulfing", "bearish", True),
    "dark_cloud_cover": ("Dark cloud cover", "bearish", True),
    "evening_star": ("Evening star", "bearish", True),
    "three_black_crows": ("Three black crows", "bearish", True),
    "bearish_harami": ("Bearish harami", "bearish", True),
    "tweezer_top": ("Tweezer top", "bearish", True),
    "gravestone_doji": ("Gravestone doji", "bearish", False),
    "bearish_marubozu": ("Bearish marubozu", "bearish", False),
    "doji": ("Doji", "neutral", False),
    "spinning_top": ("Spinning top", "neutral", False),
    "inside_bar": ("Inside bar", "neutral", False),
    "outside_bar": ("Outside bar", "neutral", False),
    "gap_up": ("Gap up", "bullish", False),
    "gap_down": ("Gap down", "bearish", False),
    "any_bullish": ("Any bullish reversal pattern", "bullish", False),
    "any_bearish": ("Any bearish reversal pattern", "bearish", False),
}
BULLISH_REVERSALS = ["hammer", "inverted_hammer", "bullish_engulfing", "piercing_line", "morning_star",
                     "three_white_soldiers", "bullish_harami", "tweezer_bottom", "dragonfly_doji"]
BEARISH_REVERSALS = ["shooting_star", "hanging_man", "bearish_engulfing", "dark_cloud_cover", "evening_star",
                     "three_black_crows", "bearish_harami", "tweezer_top", "gravestone_doji"]


def _parts(df: pd.DataFrame):
    o, h, l, c = (df[k].astype(float) for k in ("open", "high", "low", "close"))
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    avg_body = body.rolling(10, min_periods=3).mean().shift(1)
    return o, h, l, c, rng, body, upper, lower, avg_body


def _session_open(df: pd.DataFrame) -> pd.Series:
    """True on bars that start a new day. On daily candles that is every bar; on intraday candles only the first
    candle of each session, because a gap is the overnight jump, not a wiggle between two 5-minute bars."""
    if len(df) < 2:
        return pd.Series(True, index=df.index)
    spacing = pd.Series(df.index).diff().median()
    if spacing >= pd.Timedelta(hours=20):
        return pd.Series(True, index=df.index)
    idx = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index
    d = pd.Series(idx.date, index=df.index)
    return d != d.shift(1)


def detect(df: pd.DataFrame, name: str) -> pd.Series:
    if name not in PATTERNS:
        raise ValueError(f"unknown candlestick pattern {name!r}")
    if name == "any_bullish":
        return _any(df, BULLISH_REVERSALS)
    if name == "any_bearish":
        return _any(df, BEARISH_REVERSALS)
    o, h, l, c, rng, body, upper, lower, avg = _parts(df)
    avg_rng = rng.rolling(14, min_periods=5).mean().shift(1)
    normal = rng >= avg_rng * 0.8  # ignore tiny candles: their shape means little
    # net move over the previous TREND bars in average-candle-ranges: "after a fall" means a real fall
    move = (c.shift(1) - c.shift(1 + TREND)) / avg_rng
    down, up = move <= -1.0, move >= 1.0
    green, red = c > o, c < o
    small = body <= rng * 0.3
    doji = body <= rng * 0.1
    long_body = body >= avg * 1.2
    po, pc, ph, pl, pbody = o.shift(1), c.shift(1), h.shift(1), l.shift(1), body.shift(1)
    pgreen, pred = pc > po, pc < po
    mid_prev = (po + pc) / 2
    hammer_shape = (lower >= body * 2) & (lower >= rng * 0.6) & (upper <= rng * 0.15) & small & normal
    inv_shape = (upper >= body * 2) & (upper >= rng * 0.6) & (lower <= rng * 0.15) & small & normal
    tol = rng.rolling(10, min_periods=3).mean().shift(1) * 0.05
    # a rejection candle has to be AT the extreme: its low is the lowest of the last TREND+1 candles (bottom
    # patterns) or its high the highest (top patterns). Checked against TA-Lib: without this, hammers show up
    # half-way down a candle after a fall, where nothing was actually rejected.
    at_low = l <= l.rolling(TREND + 1, min_periods=2).min() + tol
    at_high = h >= h.rolling(TREND + 1, min_periods=2).max() - tol
    out = {
        "doji": doji,
        "spinning_top": ~doji & (body <= rng * 0.3) & (upper >= body) & (lower >= body),
        "dragonfly_doji": doji & (upper <= rng * 0.1) & (lower >= rng * 0.6) & down & at_low,
        "gravestone_doji": doji & (lower <= rng * 0.1) & (upper >= rng * 0.6) & up & at_high,
        "hammer": hammer_shape & down & at_low,
        "hanging_man": hammer_shape & up & at_high,
        "inverted_hammer": inv_shape & down & at_low,
        "shooting_star": inv_shape & up & at_high,
        "bullish_engulfing": pred & green & (c >= po) & (o <= pc) & (body > pbody) & down,
        "bearish_engulfing": pgreen & red & (o >= pc) & (c <= po) & (body > pbody) & up,
        "piercing_line": pred & (pbody >= avg) & green & (o < pc) & (c > mid_prev) & (c < po) & down,
        "dark_cloud_cover": pgreen & (pbody >= avg) & red & (o > pc) & (c < mid_prev) & (c > po) & up,
        "bullish_harami": pred & (pbody >= avg) & green & (o > pc) & (c < po) & (body < pbody * 0.6) & down,
        "bearish_harami": pgreen & (pbody >= avg) & red & (o < pc) & (c > po) & (body < pbody * 0.6) & up,
        "tweezer_bottom": pred & green & ((l - pl).abs() <= tol) & down & at_low,
        "tweezer_top": pgreen & red & ((h - ph).abs() <= tol) & up & at_high,
        "bullish_marubozu": green & long_body & (upper <= rng * 0.05) & (lower <= rng * 0.05),
        "bearish_marubozu": red & long_body & (upper <= rng * 0.05) & (lower <= rng * 0.05),
        "inside_bar": (h < ph) & (l > pl),
        "outside_bar": (h > ph) & (l < pl),
        "gap_up": (l > ph) & _session_open(df),
        "gap_down": (h < pl) & _session_open(df),
    }
    if name in out:
        return out[name].fillna(False).astype(bool)
    # three-candle patterns
    o2, c2, b2 = o.shift(2), c.shift(2), body.shift(2)
    if name == "morning_star":
        r = (c2 < o2) & (b2 >= avg) & (pbody <= b2 * 0.4) & (np.maximum(po, pc) < c2) & green & \
            (c > (o2 + c2) / 2) & (move.shift(1) <= -1.0)
    elif name == "evening_star":
        r = (c2 > o2) & (b2 >= avg) & (pbody <= b2 * 0.4) & (np.minimum(po, pc) > c2) & red & \
            (c < (o2 + c2) / 2) & (move.shift(1) >= 1.0)
    elif name == "three_white_soldiers":
        g = green & (body >= avg * 0.8) & (upper <= body * 0.5)
        r = g & g.shift(1, fill_value=False) & g.shift(2, fill_value=False) & (c > pc) & (pc > c2) & \
            (o > po) & (o < pc) & (po > o2) & (po < c2)
    elif name == "three_black_crows":
        b = red & (body >= avg * 0.8) & (lower <= body * 0.5)
        r = b & b.shift(1, fill_value=False) & b.shift(2, fill_value=False) & (c < pc) & (pc < c2) & \
            (o < po) & (o > pc) & (po < o2) & (po > c2)
    else:
        raise ValueError(name)
    return r.fillna(False).astype(bool)


def _any(df: pd.DataFrame, names: list[str]) -> pd.Series:
    out = pd.Series(False, index=df.index)
    for n in names:
        out |= detect(df, n)
    return out


def found(df: pd.DataFrame, last: int | None = None, chart_only: bool = False) -> list[dict]:
    """Every pattern on the last `last` bars: [{"i", "ts", "name", "label", "direction"}], oldest first."""
    view = df if last is None else df.iloc[-(last + 3 + TREND + 10):]  # enough history for the trend checks
    hits = []
    for name, (label, direction, chart) in PATTERNS.items():
        if name.startswith("any_") or (chart_only and not chart):
            continue
        s = detect(view, name)
        if last is not None:
            s = s.iloc[-last:]
        for ts in s.index[s.values]:
            hits.append({"ts": ts, "name": name, "label": label, "direction": direction})
    return sorted(hits, key=lambda x: x["ts"])


# ---------------------------------------------------------------- trendlines
def _pivots(series: pd.Series, high: bool) -> list[tuple[int, float, int]]:
    """Swing points as (bar index, price, index of the bar where it became known)."""
    v = series.to_numpy(dtype=float)
    out = []
    for i in range(PIVOT, len(v) - PIVOT):
        w = v[i - PIVOT: i + PIVOT + 1]
        if np.isnan(w).any():
            continue
        if (high and v[i] == w.max() and (w < v[i]).sum() >= 2 * PIVOT - 1) or \
                (not high and v[i] == w.min() and (w > v[i]).sum() >= 2 * PIVOT - 1):
            out.append((i, float(v[i]), i + PIVOT))
    return out


def trendline(df: pd.DataFrame, n: int, kind: str) -> pd.Series:
    """Value of the support (kind='support', through swing lows) or resistance (through swing highs) line at
    each bar, using the two latest swing points known at that bar within the last n bars. NaN when < 2 points."""
    piv = _pivots(df["low" if kind == "support" else "high"], high=kind != "support")
    out = np.full(len(df), np.nan)
    j = 0
    known: list[tuple[int, float]] = []
    for t in range(len(df)):
        while j < len(piv) and piv[j][2] <= t - 1:  # confirmed by the previous bar: no look-ahead
            known.append(piv[j][:2])
            j += 1
        pts = [p for p in known[-6:] if p[0] >= t - n]
        if len(pts) < 2:
            continue
        (i1, p1), (i2, p2) = pts[-2], pts[-1]
        if i2 == i1:
            continue
        out[t] = p2 + (p2 - p1) / (i2 - i1) * (t - i2)
    return pd.Series(out, index=df.index)


def trendline_segments(df: pd.DataFrame, n: int = 60) -> list[dict]:
    """The current support and resistance lines for drawing: two anchors + the projection to the last bar."""
    segs = []
    last = len(df) - 1
    for kind, high in (("support", False), ("resistance", True)):
        piv = [p for p in _pivots(df["high" if high else "low"], high) if p[2] <= last and p[0] >= last - n]
        if len(piv) < 2:
            continue
        (i1, p1, _), (i2, p2, _) = piv[-2], piv[-1]
        slope = (p2 - p1) / (i2 - i1)
        segs.append({"kind": kind, "points": [(df.index[i1], p1), (df.index[i2], p2), (df.index[last], p2 + slope * (last - i2))],
                     "slope_pct_per_bar": round(slope / p2 * 100, 3) if p2 else None})
    return segs


def summary(df: pd.DataFrame, since=None, latest: int = 6) -> dict:
    """Compact read of a stretch of candles (default: all): how many bullish / bearish patterns, which ones, the most
    recent, and where price sits against its trendlines. This is what the news AI is shown for each timeframe."""
    if df is None or len(df) < 20:
        return {"bars": 0 if df is None else len(df), "available": False}
    n = int((df.index >= since).sum()) if since is not None else len(df)
    hits = found(df, last=max(1, n))
    directional = [h for h in hits if h["direction"] != "neutral"]
    bull = sum(h["direction"] == "bullish" for h in directional)
    bear = len(directional) - bull
    counts: dict[str, int] = {}
    for h in directional:
        counts[h["label"]] = counts.get(h["label"], 0) + 1
    tail = df.iloc[-300:]
    last = float(tail["close"].iloc[-1])
    sup, res = trendline(tail, 60, "support").iloc[-1], trendline(tail, 60, "resistance").iloc[-1]
    if res == res and last > res:
        where = f"above trendline resistance {res:.2f} (breakout)"
    elif sup == sup and last < sup:
        where = f"below trendline support {sup:.2f} (breakdown)"
    elif sup == sup and res == res:
        where = f"between trendline support {sup:.2f} and resistance {res:.2f}"
    else:
        where = "no clear trendline"
    recent = directional[-latest:]
    return {
        "available": True, "bars": n, "bullish": bull, "bearish": bear,
        "bias": "bullish" if bull > bear * 1.3 else "bearish" if bear > bull * 1.3 else "mixed",
        "most_common": sorted(counts.items(), key=lambda kv: -kv[1])[:5],
        "latest": [{"pattern": h["label"], "direction": h["direction"], "at": h["ts"].isoformat()} for h in recent],
        "trendline": where,
    }
