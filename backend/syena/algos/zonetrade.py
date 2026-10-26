"""Zone retest strategy: Umar Punjabi's price-action method, made mechanical and testable.

What he teaches (videos: chart reading course, Fibonacci, daily market direction, liquidity, "my exact strategy"):

1. Bias first. Read structure on the higher timeframe: a close above the last swing high is a bullish break of
   structure (BOS) and makes you "buy team"; a close below the last swing low makes you "sell team". The bias stays
   until the opposite break. Never trade against it. Syena trades cash equity long-only here, so a bearish bias
   simply means no new buys.
2. Only strong zones. A demand zone is valid only when price left it with a strong move (here at least 2x ATR within
   15 candles) AND that move broke structure (made a higher high than the swing high before it). The origin of the
   big move matters, not a level with many weak touches. A swing high broken aggressively (a big candle or a close
   well beyond it) flips into a demand zone too.
3. Liquidity. Equal lows and "obvious" levels tested again and again hold resting stop-losses and tend to be swept
   before the real move. So: a zone already visited twice is skipped unless the visit sweeps liquidity (wicks below
   the zone or below equal lows, then closes back above). A sweep is a quality bonus.
4. Entry only on confirmation. Price comes back into the zone and closes with a bullish candle (close in the upper
   half of its range, above the open) or a bullish reversal pattern. No confirmation, no trade.
5. Risk. Stop beyond the zone (and beyond the confirmation candle's wick). Target the next strong opposite
   (resistance) zone, just before it. The trade is taken only if that gives at least 1:2. When the path to the next
   resistance is a "clear area" (1:4 or more, or no resistance at all), he holds for the bigger move instead of
   booking at 1:2; the 'zone' target mode does that, 'fixed2' always books at 1:2.
6. Fibonacci confluence: a retest that lands in the 0.5-0.786 retracement of the latest up-leg is flagged (and can be
   required).

No look-ahead: everything at candle t uses candles up to t only (swings count once their confirming candles have
closed; the higher-timeframe bias uses completed higher-timeframe candles only). Signals fire on t's close; the
backtester fills at t+1's open.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from syena.algos import indicators as ind
from syena.algos import levels as lv
from syena.algos import patterns as pat

NAME = "zone_retest"
STRONG_ATR = 2.0      # the move away from a zone must reach this many ATR...
MOVE_BARS = 15        # ...within this many candles
ZONE_ATR = 0.6        # max zone depth above the swing low, in ATR
LEFT_ATR = 1.5        # price must have travelled this far above the zone before a retest counts
FLIP_BODY_ATR = 0.8   # an "aggressive" break: candle body of this many ATR, or a close this far beyond the level
FLIP_CLOSE_ATR = 0.5
EQUAL_ATR = 0.15      # swing lows within this distance are "equal lows" (a liquidity pool)
MAX_VISITS = 2
CLEAR_R = 4.0
CLEAR_SKY_R = 3.0     # target when there is no resistance above at all
ZONE_LIFETIME = 250

DEFAULTS = {"min_rr": 2.0, "target_mode": "zone", "htf": True, "require_fib": False, "require_sweep": False}
TARGET_MODES = ("zone", "fixed2")


def canon_params(p: dict | None) -> dict:
    p = dict(DEFAULTS, **(p or {}))
    out = {"min_rr": float(p["min_rr"]), "target_mode": str(p["target_mode"]), "htf": bool(p["htf"]),
           "require_fib": bool(p["require_fib"]), "require_sweep": bool(p["require_sweep"])}
    if not 1.0 <= out["min_rr"] <= 6.0:
        raise ValueError("min reward:risk must be between 1 and 6")
    if out["target_mode"] not in TARGET_MODES:
        raise ValueError(f"target_mode must be one of {TARGET_MODES}")
    return out


def describe(p: dict) -> str:
    bits = [f"Zone retest (Umar Punjabi method): bullish structure{' on this and the higher timeframe' if p['htf'] else ''}, "
            f"price back in a strong demand zone (origin of a move that broke structure, or an aggressively broken "
            f"resistance), bullish confirmation candle, room to the next resistance of at least 1:{p['min_rr']:g}"]
    if p["require_fib"]:
        bits.append("retest inside the 0.5-0.786 Fibonacci pullback")
    if p["require_sweep"]:
        bits.append("only after a liquidity sweep")
    return "; ".join(bits)


def describe_target(p: dict) -> str:
    return ("just before the next strong resistance zone (holds through clear areas)" if p["target_mode"] == "zone"
            else f"1:{p['min_rr']:g} reward:risk (only taken when the next resistance allows it)")


@dataclass
class DZone:
    low: float
    high: float
    kind: str            # origin | flip
    origin: int          # candle of the swing / break
    created: int
    top_since: float     # highest high since the zone formed
    visits: int = 0
    inside: bool = False
    traded_visit: int = -1
    meta: dict = field(default_factory=dict)


def _bias_series(df: pd.DataFrame, sw: list[lv.Swing]) -> np.ndarray:
    """+1 after a close above the last confirmed swing high, -1 after a close below the last swing low; 0 before."""
    c = df["close"].to_numpy(float)
    out = np.zeros(len(df), int)
    by_known: dict[int, list[lv.Swing]] = {}
    for s in sw:
        by_known.setdefault(s.known, []).append(s)
    last_h = last_l = None
    b = 0
    for t in range(len(df)):
        for s in by_known.get(t, []):
            if s.kind == "H":
                last_h = s.price
            else:
                last_l = s.price
        if last_h is not None and c[t] > last_h:
            b = 1
        elif last_l is not None and c[t] < last_l:
            b = -1
        out[t] = b
    return out


def _htf_bias(df: pd.DataFrame) -> np.ndarray:
    """Higher-timeframe bias for each candle, from COMPLETED higher-timeframe candles only (weekly for daily charts,
    daily for intraday). 0 when there isn't enough history."""
    n = len(df)
    hdf = lv._htf(df)
    if hdf is None or len(hdf) < 2 * lv.HTF_SWING + 3:
        return np.zeros(n, int)
    hb = _bias_series(hdf, lv.swings(hdf, lv.HTF_SWING))
    idx = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index
    # weekly bars are labelled by their Friday, daily bars by their date: either way a higher-timeframe bar is
    # complete for every candle on a later calendar day
    labels = hdf.index.normalize()
    days = idx.normalize()
    out = np.zeros(n, int)
    k = -1
    for t in range(n):
        while k + 1 < len(hdf) and labels[k + 1] < days[t]:
            k += 1
        out[t] = hb[k] if k >= 0 else 0
    return out


def _resistance_target(df: pd.DataFrame, t: int, entry: float, sw, atr) -> float | None:
    """Bottom of the nearest medium/strong resistance zone above the entry, as it looked at candle t."""
    zones = lv.zones_at(df, upto=t, sw=sw, atr=atr)
    above = sorted((z for z in zones if z.low > entry and z.score >= 3.5), key=lambda z: z.low)
    return above[0].low if above else None


def signals(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """One row per candle: whether the setup fires on that candle's close, with its stop, target and reasons."""
    p = canon_params(params)
    n = len(df)
    cols = {"signal": np.zeros(n, bool), "stop": np.full(n, np.nan), "target": np.full(n, np.nan), "rr": np.full(n, np.nan),
            "clear": np.zeros(n, bool), "sweep": np.zeros(n, bool), "fib": np.zeros(n, bool), "bias": np.zeros(n, int),
            "htf_bias": np.zeros(n, int), "zone_low": np.full(n, np.nan), "zone_high": np.full(n, np.nan)}
    reasons = [""] * n
    if n < 2 * lv.SWING + 30:
        return pd.DataFrame(dict(cols, reason=reasons), index=df.index)
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = ind.atr(df, 14).to_numpy(float)
    sw = lv.swings(df)
    bias = _bias_series(df, sw)
    hbias = _htf_bias(df) if p["htf"] else np.ones(n, int)
    bull_pat = pat.detect(df, "any_bullish").to_numpy(bool)
    cols["bias"], cols["htf_bias"] = bias, hbias

    by_known: dict[int, list[lv.Swing]] = {}
    for s in sw:
        by_known.setdefault(s.known, []).append(s)
    known_h: list[lv.Swing] = []       # confirmed swing highs, in order of their candle
    known_l: list[lv.Swing] = []
    pending: list[tuple[lv.Swing, float]] = []   # swing lows waiting to prove a strong, structure-breaking move
    zones: list[DZone] = []
    flipped: set[int] = set()
    last_h_for_bos = None

    for t in range(n):
        a = atr[t]
        if not a == a or a <= 0:
            continue
        for s in by_known.get(t, []):
            if s.kind == "H":
                known_h.append(s)
                last_h_for_bos = s
            else:
                known_l.append(s)
                prior = [x for x in known_h if x.i < s.i]
                pending.append((s, prior[-1].price if prior else np.inf))
        # 1) swing lows that now qualify as demand zones (strong move that broke structure)
        still = []
        for s, prior_high in pending:
            if t - s.i > MOVE_BARS:
                continue
            top = h[s.i + 1:t + 1].max() if t > s.i else s.price
            a0 = atr[s.i] if atr[s.i] == atr[s.i] and atr[s.i] > 0 else a
            if top - s.price >= STRONG_ATR * a0 and top > prior_high:
                body_top = max(o[s.i], c[s.i])
                hi = min(s.price + ZONE_ATR * a0, max(body_top, s.price + 0.25 * a0))
                if not any(z.low <= hi and s.price <= z.high for z in zones):  # overlapping zones are one area
                    zones.append(DZone(s.price, hi, "origin", s.i, t, top, meta={"move_atr": round((top - s.price) / a0, 1)}))
            else:
                still.append((s, prior_high))
        pending = still
        # 2) an aggressive break of the last swing high flips it into demand
        if last_h_for_bos is not None and last_h_for_bos.i not in flipped and c[t] > last_h_for_bos.price \
                and (c[t] - o[t] >= FLIP_BODY_ATR * a or c[t] >= last_h_for_bos.price + FLIP_CLOSE_ATR * a):
            flipped.add(last_h_for_bos.i)
            lvl = last_h_for_bos.price
            if not any(z.low <= lvl and lvl - 0.5 * a <= z.high for z in zones):
                zones.append(DZone(lvl - 0.5 * a, lvl, "flip", t, t, h[t]))
        # 3) maintain zones; look for a confirmed retest
        best = None
        alive = []
        for z in zones:
            if c[t] < z.low or t - z.created > ZONE_LIFETIME:
                continue  # broken (closed below) or stale
            alive.append(z)
            if z.created >= t:
                continue
            touching = l[t] <= z.high
            if touching and not z.inside:
                z.visits += 1
            z.inside = touching
            left = z.top_since >= z.high + LEFT_ATR * a
            z.top_since = max(z.top_since, h[t])
            if not touching or not left or z.traded_visit == z.visits:
                continue
            if best is None or z.high > best.high:  # the nearest zone below price is the one being tested
                best = z
        zones = alive
        if best is None or bias[t] != 1 or hbias[t] == -1:
            continue
        z = best
        rng = h[t] - l[t]
        confirmed = (c[t] > o[t] and rng > 0 and c[t] >= l[t] + 0.5 * rng and c[t] > z.low) or (bull_pat[t] and c[t] > z.low)
        if not confirmed:
            continue
        # liquidity: wick below the zone, or below equal lows, then close back above
        pools = []
        recent = [s.price for s in known_l if s.i >= t - lv.LOOKBACK]
        for i1 in range(len(recent)):
            for i2 in range(i1 + 1, len(recent)):
                if abs(recent[i1] - recent[i2]) <= EQUAL_ATR * a:
                    pools.append(min(recent[i1], recent[i2]))
        sweep = l[t] < z.low or any(l[t] < pv < c[t] for pv in pools if pv >= c[t] - 2 * a)
        if z.visits > MAX_VISITS and not sweep:
            continue
        if p["require_sweep"] and not sweep:
            continue
        # Fibonacci: latest up-leg = last confirmed swing low -> highest high after it
        fib = False
        if known_l:
            leg_low = known_l[-1]
            if leg_low.i < t:
                leg_high = h[leg_low.i:t].max() if t > leg_low.i else leg_low.price
                span = leg_high - leg_low.price
                if span > a:
                    fib = leg_high - 0.786 * span <= l[t] <= leg_high - 0.5 * span
        if p["require_fib"] and not fib:
            continue
        stop = min(z.low, l[t]) - 0.1 * a
        entry = c[t]
        risk = entry - stop
        if risk <= 0:
            continue
        res = _resistance_target(df, t, entry, sw, atr)
        if res is None:
            room_r, clear = CLEAR_SKY_R, True
            zone_target = entry + CLEAR_SKY_R * risk
        else:
            zone_target = res - 0.05 * a
            room_r = (zone_target - entry) / risk
            clear = room_r >= CLEAR_R
        if room_r < p["min_rr"]:
            continue
        target = zone_target if p["target_mode"] == "zone" else entry + p["min_rr"] * risk
        z.traded_visit = z.visits
        for other in zones:  # one trade per area per visit
            if other is not z and other.low <= z.high and z.low <= other.high:
                other.traded_visit = other.visits
        cols["signal"][t] = True
        cols["stop"][t], cols["target"][t] = round(stop, 2), round(target, 2)
        cols["rr"][t] = round((target - entry) / risk, 2)
        cols["clear"][t], cols["sweep"][t], cols["fib"][t] = clear, sweep, fib
        cols["zone_low"][t], cols["zone_high"][t] = round(z.low, 2), round(z.high, 2)
        why = [f"bullish structure{' (higher timeframe agrees)' if p['htf'] and hbias[t] == 1 else ''}",
               f"retest #{z.visits} of {'demand zone at the origin of a ' + str(z.meta.get('move_atr')) + '×ATR move' if z.kind == 'origin' else 'old resistance flipped to support'} "
               f"{z.low:.2f}–{z.high:.2f}", "bullish confirmation candle"]
        if sweep:
            why.append("liquidity swept below the zone / equal lows")
        if fib:
            why.append("inside the 0.5–0.786 Fibonacci pullback")
        why.append("clear area to the next resistance" if clear else f"room to next resistance 1:{room_r:.1f}")
        reasons[t] = "; ".join(why)
    out = pd.DataFrame(dict(cols, reason=reasons), index=df.index)
    price = c[-1]
    waiting = sorted((z for z in zones if z.high < price and z.top_since >= z.high + LEFT_ATR * atr[-1]),
                     key=lambda z: -z.high)[:2]
    out.attrs["zones"] = [{"low": round(float(z.low), 2), "high": round(float(z.high), 2), "kind": z.kind, "visits": z.visits,
                           "distance_pct": round(float(z.high / price - 1) * 100, 2)} for z in waiting]
    return out


def current_setup(df: pd.DataFrame, params: dict | None = None) -> dict:
    """What the strategy sees on the latest candle, for the Stock page: bias, the demand zone it is waiting for,
    and whether it fired."""
    p = canon_params(params)
    if df is None or len(df) < 2 * lv.SWING + 30:
        return {"available": False}
    sig = signals(df, p)
    last = sig.iloc[-1]
    recent = sig[sig["signal"]].tail(5)
    bias = {1: "bullish (buy team)", -1: "bearish (sell team: no new buys)", 0: "not set yet"}
    out = {"available": True, "bias": bias[int(last["bias"])], "htf_bias": bias[int(last["htf_bias"])] if p["htf"] else None,
           "fired_today": bool(last["signal"]), "waiting_zones": sig.attrs.get("zones", []),
           "recent": [{"ts": i.isoformat(), "stop": float(r["stop"]), "target": float(r["target"]), "rr": float(r["rr"]), "why": r["reason"]}
                      for i, r in recent.iterrows()]}
    if last["signal"]:
        out.update({"stop": float(last["stop"]), "target": float(last["target"]), "rr": float(last["rr"]), "why": last["reason"]})
    return out
