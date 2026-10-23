"""Support and resistance zones, and market structure, the way price-action traders read a chart.

Zones, not lines: price reacts to an area. A zone is built from swing points (a candle whose high, or low, is the
extreme of the candles around it) that sit close together. "Close" is measured in ATR, the stock's typical daily
range, so the same rule works for a ₹50 stock and a ₹5,000 stock, on 5-minute candles and on daily ones.

A zone gets stronger with:
  - more touches (separate swing points inside it);
  - bigger reactions (how far price moved away after touching it, in ATR);
  - recent touches (old levels fade);
  - role reversal: it was resistance, price broke it, and now it holds as support (or the other way round);
  - agreement with the higher timeframe (weekly swings on a daily chart, daily swings on an intraday chart).

Market structure labels each swing: HH/LH (higher / lower high) and HL/LL (higher / lower low). Higher highs with
higher lows is an uptrend, lower highs with lower lows a downtrend, anything else a range. A close beyond the last
swing high (or low) is a break of structure.

Nothing here looks at future candles: a swing point only counts once the candles that confirm it have closed, and
the rolling series used by algos pick levels relative to the previous candle's close.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from syena.algos import indicators as ind

SWING = 3            # candles on each side that a swing high/low must beat
HTF_SWING = 2
LOOKBACK = 250       # candles of history the zones are built from
MERGE_ATR = 1.0      # swing points closer than this (in ATR) belong to one zone
MIN_WIDTH_ATR = 0.25
REACTION_BARS = 10   # how far ahead the reaction after a touch is measured


@dataclass
class Swing:
    i: int           # candle where the swing is
    price: float
    kind: str        # "H" (swing high) or "L" (swing low)
    known: int       # candle at whose close it is confirmed
    reaction: float = 0.0  # move away afterwards, in ATR (measured only with candles up to `known` + REACTION_BARS)


@dataclass
class Zone:
    low: float
    high: float
    touches: int
    highs: int       # touches that were swing highs (acted as resistance)
    lows: int        # touches that were swing lows (acted as support)
    last_touch: int
    reaction: float
    htf: bool = False
    score: float = 0.0
    swings: list = field(default_factory=list)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @property
    def flipped(self) -> bool:
        return self.highs > 0 and self.lows > 0


def swings(df: pd.DataFrame, k: int = SWING) -> list[Swing]:
    """Swing highs and lows, oldest first. A swing at candle i is known at candle i + k."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    n = len(df)
    out = []
    for i in range(k, n - k):
        win_h, win_l = h[i - k:i + k + 1], l[i - k:i + k + 1]
        if h[i] == win_h.max() and np.argmax(win_h) == k:
            out.append(Swing(i, float(h[i]), "H", i + k))
        if l[i] == win_l.min() and np.argmin(win_l) == k:
            out.append(Swing(i, float(l[i]), "L", i + k))
    out.sort(key=lambda s: (s.i, s.kind))
    return out


def _reactions(sw: list[Swing], df: pd.DataFrame, atr: np.ndarray, upto: int) -> None:
    """How far price travelled away from each swing (in ATR) within REACTION_BARS, using candles <= upto only."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    for s in sw:
        a = atr[s.i] if s.i < len(atr) and atr[s.i] == atr[s.i] and atr[s.i] > 0 else None
        end = min(upto, s.i + REACTION_BARS)
        if a is None or end <= s.i:
            s.reaction = 0.0
            continue
        if s.kind == "H":
            s.reaction = float((s.price - l[s.i + 1:end + 1].min()) / a)
        else:
            s.reaction = float((h[s.i + 1:end + 1].max() - s.price) / a)


def build_zones(points: list[Swing], atr_now: float, upto: int, htf_points: list[Swing] | None = None) -> list[Zone]:
    """Group nearby swing points into zones and score them."""
    if not points or not atr_now or atr_now != atr_now:
        return []
    tol = MERGE_ATR * atr_now
    pts = sorted(points, key=lambda s: s.price)
    groups, cur = [], [pts[0]]
    for s in pts[1:]:
        if s.price - cur[-1].price <= tol and s.price - cur[0].price <= 1.5 * tol:
            cur.append(s)
        else:
            groups.append(cur)
            cur = [s]
    groups.append(cur)
    zones = []
    for g in groups:
        lo, hi = min(s.price for s in g), max(s.price for s in g)
        if hi - lo < MIN_WIDTH_ATR * atr_now:
            m = (hi + lo) / 2
            lo, hi = m - MIN_WIDTH_ATR * atr_now / 2, m + MIN_WIDTH_ATR * atr_now / 2
        z = Zone(lo, hi, touches=len(g), highs=sum(s.kind == "H" for s in g), lows=sum(s.kind == "L" for s in g),
                 last_touch=max(s.i for s in g), reaction=float(np.mean([min(s.reaction, 4.0) for s in g])), swings=g)
        if htf_points:
            z.htf = any(z.low - 0.25 * atr_now <= s.price <= z.high + 0.25 * atr_now for s in htf_points)
        age = (upto - z.last_touch) / max(LOOKBACK, 1)
        # 0-10: touches up to 4, reaction up to 2, flip 1.5, higher timeframe 1.5, recency 1
        z.score = (min(z.touches - 1, 4) * 1.0 + min(z.reaction / 1.5, 2.0) + (1.5 if z.flipped else 0.0)
                   + (1.5 if z.htf else 0.0) + max(0.0, 1.0 - age * 2))
        # one touch is just a swing point; keep it only if price clearly turned there
        if z.touches >= 2 or z.reaction >= 2.0 or z.htf:
            zones.append(z)
    return zones


def _htf(df: pd.DataFrame) -> pd.DataFrame | None:
    """Higher timeframe candles: weekly for a daily chart, daily for intraday."""
    if len(df) < 30:
        return None
    spacing = pd.Series(df.index).diff().median()
    rule = "W-FRI" if spacing >= pd.Timedelta(hours=20) else "1D"
    idx = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index
    x = df.set_axis(idx).resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return x if len(x) >= 2 * HTF_SWING + 3 else None


def zones_at(df: pd.DataFrame, upto: int | None = None, sw: list[Swing] | None = None, atr: np.ndarray | None = None,
             htf_points: list[Swing] | None = None) -> list[Zone]:
    """Zones as they looked at the close of candle `upto` (default: the last one)."""
    n = len(df)
    upto = n - 1 if upto is None else upto
    atr = ind.atr(df, 14).to_numpy(float) if atr is None else atr
    sw = swings(df) if sw is None else sw
    known = [s for s in sw if s.known <= upto and s.i >= upto - LOOKBACK]
    _reactions(known, df, atr, upto)
    return build_zones(known, atr[upto], upto, htf_points)


def structure(df: pd.DataFrame, sw: list[Swing] | None = None) -> dict:
    """Swing labels (HH/HL/LH/LL), the trend they make, and any break of structure on the last candle."""
    sw = swings(df) if sw is None else sw
    # keep alternating swings: of consecutive highs keep the highest, of consecutive lows the lowest
    alt: list[Swing] = []
    for s in sw:
        if alt and alt[-1].kind == s.kind:
            if (s.kind == "H" and s.price > alt[-1].price) or (s.kind == "L" and s.price < alt[-1].price):
                alt[-1] = s
            continue
        alt.append(s)
    # swings within a third of a day's range of the previous one count as equal (EH / EL): that is a range, not a trend
    atr = ind.atr(df, 14).to_numpy(float) if len(df) else np.array([])
    labels, last = [], {"H": None, "L": None}
    for s in alt:
        prev = last[s.kind]
        if prev is not None:
            a = atr[s.i] if s.i < len(atr) and atr[s.i] == atr[s.i] else 0.0
            diff = s.price - prev.price
            if abs(diff) <= a / 3:
                lab = "EH" if s.kind == "H" else "EL"
            elif s.kind == "H":
                lab = "HH" if diff > 0 else "LH"
            else:
                lab = "HL" if diff > 0 else "LL"
            labels.append({"i": s.i, "price": s.price, "label": lab})
        last[s.kind] = s
    highs = [x["label"] for x in labels if x["label"] in ("HH", "LH", "EH")][-2:]
    lows = [x["label"] for x in labels if x["label"] in ("HL", "LL", "EL")][-2:]
    if highs and lows and highs[-1] == "HH" and lows[-1] == "HL":
        trend = "uptrend"
    elif highs and lows and highs[-1] == "LH" and lows[-1] == "LL":
        trend = "downtrend"
    else:
        trend = "range"
    close = float(df["close"].iloc[-1]) if len(df) else None
    brk = None
    if close is not None and last["H"] is not None and close > last["H"].price:
        brk = {"direction": "bullish", "level": last["H"].price}
    elif close is not None and last["L"] is not None and close < last["L"].price:
        brk = {"direction": "bearish", "level": last["L"].price}
    return {"trend": trend, "labels": labels,
            "last_high": last["H"].price if last["H"] else None, "last_low": last["L"].price if last["L"] else None,
            "break": brk}


def nearest(zones: list[Zone], price: float, n: int = 3) -> tuple[list[Zone], list[Zone]]:
    """Up to n support zones below `price` (nearest first) and n resistance zones above it. A zone price is inside
    counts on the side it acted on most."""
    sup = sorted([z for z in zones if z.high < price or (z.low <= price <= z.high and z.lows >= z.highs)],
                 key=lambda z: price - z.mid)
    res = sorted([z for z in zones if z.low > price or (z.low <= price <= z.high and z.highs > z.lows)],
                 key=lambda z: z.mid - price)
    # skip weak zones sandwiched closer than a stronger one on the same side
    def pick(zs):
        # nearest first, but a weak zone is skipped when a clearly stronger one is close behind it, and zones on
        # the same side stay at least one zone-width apart so the chart isn't striped
        out = []
        for k, z in enumerate(zs):
            if len(out) >= n:
                break
            behind = zs[k + 1:k + 3]
            if z.score < 3 and any(b.score >= z.score + 2 and abs(b.mid - z.mid) <= 3 * (z.high - z.low + b.high - b.low) for b in behind):
                continue
            if out and abs(z.mid - out[-1].mid) < (z.high - z.low + out[-1].high - out[-1].low) * 0.75:
                continue
            out.append(z)
        return out
    return pick(sup), pick(res)


def rolling_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Per candle: the top of the nearest support zone and the bottom of the nearest resistance zone, chosen
    relative to the PREVIOUS candle's close using only swings confirmed by this candle. So 'close crosses above
    resistance' is a breakout through a level that existed before the move."""
    n = len(df)
    sup = np.full(n, np.nan)
    res = np.full(n, np.nan)
    if n < 2 * SWING + 3:
        return pd.DataFrame({"support": sup, "resistance": res}, index=df.index)
    atr = ind.atr(df, 14).to_numpy(float)
    sw = swings(df)
    c = df["close"].to_numpy(float)
    known_at = sorted({s.known for s in sw})
    zones: list[Zone] = []
    j, last_build = 0, -1
    for t in range(1, n):
        # rebuild when a new swing is confirmed (zones only change then), or every 20 candles as reactions grow
        while j < len(known_at) and known_at[j] <= t:
            j += 1
        if j and (known_at[j - 1] > last_build or t - last_build >= 20):
            zones = zones_at(df, upto=t, sw=sw, atr=atr)
            last_build = t
        if not zones:
            continue
        s_, r_ = nearest(zones, c[t - 1], n=1)
        if s_:
            sup[t] = s_[0].high
        if r_:
            res[t] = r_[0].low
    return pd.DataFrame({"support": sup, "resistance": res}, index=df.index)


def _zone_out(z: Zone, kind: str, price: float) -> dict:
    strength = "strong" if z.score >= 6 else "medium" if z.score >= 3.5 else "weak"
    why = [f"{z.touches} touch{'es' if z.touches != 1 else ''}"]
    if z.reaction >= 1.5:
        why.append(f"bounces of ~{z.reaction:.1f}× daily range")
    if z.flipped:
        why.append("old resistance now support" if kind == "support" and z.highs >= z.lows
                   else "old support now resistance" if kind == "resistance" and z.lows >= z.highs else "acted as both support and resistance")
    if z.htf:
        why.append("also a higher-timeframe level")
    return {"kind": kind, "low": round(z.low, 2), "high": round(z.high, 2), "mid": round(z.mid, 2),
            "touches": z.touches, "strength": strength, "score": round(z.score, 1),
            "distance_pct": round((z.mid / price - 1) * 100, 2), "why": ", ".join(why)}


def analyse(df: pd.DataFrame) -> dict:
    """Current zones (3 each side), market structure and a one-line reading, from all candles in df."""
    if df is None or len(df) < 2 * SWING + 10:
        return {"supports": [], "resistances": [], "structure": None, "reading": ""}
    sw = swings(df)
    hdf = _htf(df)
    htf_points = swings(hdf, HTF_SWING) if hdf is not None else None
    zones = zones_at(df, sw=sw, htf_points=htf_points)
    price = float(df["close"].iloc[-1])
    sup, res = nearest(zones, price)
    st = structure(df, sw)
    parts = [f"{st['trend']}"]
    if st["break"]:
        against = (st["trend"] == "downtrend" and st["break"]["direction"] == "bullish") or \
                  (st["trend"] == "uptrend" and st["break"]["direction"] == "bearish")
        parts.append(f"{st['break']['direction']} {'change of character (trend may be turning)' if against else 'break of structure'}"
                     f" through {st['break']['level']:.2f}")
    atr_now = float(ind.atr(df, 14).iloc[-1] or 0)
    if sup:
        z = sup[0]
        d = (price - z.high) / atr_now if atr_now else 9
        parts.append(("at" if price <= z.high else "near" if d <= 1 else "above") + f" support {z.low:.2f}–{z.high:.2f} ({z.touches} touches)")
    if res:
        z = res[0]
        d = (z.low - price) / atr_now if atr_now else 9
        parts.append(("at" if price >= z.low else "just under" if d <= 1 else "below") + f" resistance {z.low:.2f}–{z.high:.2f} ({z.touches} touches)")
    elif price >= float(df["high"].iloc[-LOOKBACK:].max()) * 0.98:
        parts.append("near the high of the period: no resistance above")
    return {"supports": [_zone_out(z, "support", price) for z in sup],
            "resistances": [_zone_out(z, "resistance", price) for z in res],
            "structure": st, "reading": "; ".join(parts), "atr": atr_now, "price": price}


def levels_payload(df: pd.DataFrame, interval: str) -> dict:
    """What the chart draws: zones as bands, swing labels as markers, the trend."""
    a = analyse(df)
    st = a.get("structure") or {}
    t = lambda i: int(df.index[i].timestamp())  # noqa: E731
    return {"supports": a["supports"], "resistances": a["resistances"], "reading": a["reading"],
            "trend": st.get("trend"), "break": st.get("break"),
            "swings": [{"t": t(x["i"]), "price": round(x["price"], 2), "label": x["label"]} for x in (st.get("labels") or [])][-40:]}


def score_for_trade(df: pd.DataFrame, side: str) -> tuple[float, list[str]]:
    """How the current position relative to zones and structure suits a trade, -1..+1, with reasons.
    A long likes: sitting on support, an uptrend, a breakout above resistance; it dislikes buying right under
    resistance or in a downtrend. A short is the mirror image."""
    a = analyse(df)
    if not a.get("structure"):
        return 0.0, []
    price, atr_now = a["price"], a["atr"] or 0
    st = a["structure"]
    pts, why = 0.0, []
    trend_pts = {"uptrend": 0.35, "downtrend": -0.35}.get(st["trend"], 0.0)
    if trend_pts:
        pts += trend_pts
        why.append(f"market structure: {st['trend']}")
    if st["break"]:
        b = 0.3 if st["break"]["direction"] == "bullish" else -0.3
        pts += b
        why.append(f"{st['break']['direction']} break of structure at {st['break']['level']:.2f}")
    if atr_now:
        if a["supports"]:
            z = a["supports"][0]
            d = (price - z["high"]) / atr_now
            if d <= 0.5:
                pts += 0.35 if z["strength"] != "weak" else 0.2
                why.append(f"sitting on {z['strength']} support {z['low']}–{z['high']} ({z['why']})")
        if a["resistances"]:
            z = a["resistances"][0]
            d = (z["low"] - price) / atr_now
            if d <= 0.5:
                pts -= 0.35 if z["strength"] != "weak" else 0.2
                why.append(f"right under {z['strength']} resistance {z['low']}–{z['high']} ({z['why']})")
    pts = max(-1.0, min(1.0, pts))
    return (pts if side == "BUY" else -pts), why
