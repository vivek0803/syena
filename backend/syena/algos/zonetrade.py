"""Zone confluence strategy: Syena's own swing setup built on its support/resistance engine.

Price-action traders agree on a few ideas (trade with the trend, buy at strong demand, wait for confirmation, put
the stop beyond the zone and aim for the next resistance); Syena turns them into a scored, testable rule instead of
a yes/no checklist, and lets the backtests decide what matters on NSE stocks.

A setup is looked for when price comes back into a support area and a bullish candle closes there. Each setup gets a
confluence score out of 100:

  trend (25)        daily structure bullish (close above the last swing high, until a close below the last swing
                    low) 15, close above the 50-candle average 5, higher timeframe (weekly / daily) bullish 5.
                    A bearish structure is a veto: Syena trades cash shares long-only here, so no new buys.
  zone quality (25) from Syena's zone engine (touches, bounce size, role reversal, higher-timeframe level, recency),
                    or a demand zone where a 2×ATR+ move that broke structure started, or a resistance broken by a
                    big candle; overlapping kinds score a confluence bonus.
  confirmation (20) a bullish reversal pattern (engulfing, morning star, three inside/outside up...) 20, a plain
                    strong close (green, in the upper half of its range) 12. No confirmation, no trade.
  volume (10)       confirmation candle on 1.2× its 20-candle average volume 10, above average 5.
  liquidity (10)    the candle first dipped below the zone or below equal lows, then closed back above (stops taken).
  extras (10)       retest inside the 0.5-0.786 Fibonacci pullback of the last up-leg 5; RSI 35-65 (room to run) 5.

A zone already tested four or more times without a dip below it is skipped (stops cluster under obvious levels).
The trade is taken when the score reaches min_score and the next strong resistance leaves at least min_rr reward for
the risk. Stop just beyond the zone and the candle's low; target just before that resistance ('zone' mode, which
holds through open space) or a fixed min_rr multiple ('fixed').

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

NAME = "zone_retest"  # stored in saved rules: keep the key stable
LABEL = "Zone confluence"
STRONG_ATR = 2.0      # a demand zone's move away must reach this many ATR...
MOVE_BARS = 15        # ...within this many candles
ZONE_ATR = 0.6        # max zone depth above the swing low, in ATR
LEFT_ATR = 1.2        # price must have been this far above the zone recently, so this is a retest from above
FLIP_BODY_ATR = 0.8   # a "big candle" break: body of this many ATR, or a close this far beyond the level
FLIP_CLOSE_ATR = 0.5
EQUAL_ATR = 0.15      # swing lows within this distance are "equal lows"
MAX_TOUCHES = 4
CLEAR_R = 4.0
CLEAR_SKY_R = 3.0     # target when there is no resistance above at all
ZONE_LIFETIME = 250
REBUILD_EVERY = 20

DEFAULTS = {"min_rr": 2.0, "target_mode": "zone", "htf": True, "require_fib": False, "require_sweep": False,
            "min_score": 60, "require_volume": False}
TARGET_MODES = ("zone", "fixed", "fixed2")  # 'fixed2' = older name of 'fixed'


def canon_params(p: dict | None) -> dict:
    p = dict(DEFAULTS, **(p or {}))
    out = {"min_rr": float(p["min_rr"]), "target_mode": "fixed" if p["target_mode"] == "fixed2" else str(p["target_mode"]),
           "htf": bool(p["htf"]), "require_fib": bool(p["require_fib"]), "require_sweep": bool(p["require_sweep"]),
           "min_score": int(p["min_score"]), "require_volume": bool(p["require_volume"])}
    if not 1.0 <= out["min_rr"] <= 6.0:
        raise ValueError("min reward:risk must be between 1 and 6")
    if out["target_mode"] not in TARGET_MODES:
        raise ValueError(f"target_mode must be one of {TARGET_MODES}")
    if not 30 <= out["min_score"] <= 95:
        raise ValueError("min score must be between 30 and 95")
    return out


def describe(p: dict) -> str:
    bits = [f"{LABEL} setup scoring at least {p['min_score']}/100: price back in a support zone with a bullish "
            f"confirmation candle, structure bullish{' on this and the higher timeframe' if p['htf'] else ''}, "
            f"room to the next resistance of at least 1:{p['min_rr']:g}"]
    if p["require_volume"]:
        bits.append("confirmation on above-average volume")
    if p["require_fib"]:
        bits.append("retest inside the 0.5-0.786 Fibonacci pullback")
    if p["require_sweep"]:
        bits.append("only after the lows were swept")
    return "; ".join(bits)


def describe_target(p: dict) -> str:
    return ("just before the next strong resistance zone (holds through open space)" if p["target_mode"] == "zone"
            else f"1:{p['min_rr']:g} reward:risk (only taken when the next resistance allows it)")


@dataclass
class DZone:
    low: float
    high: float
    kind: str            # origin | flip
    origin: int
    created: int
    top_since: float
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
    """One row per candle: whether the setup fires on that candle's close, its score, stop, target and reasons."""
    p = canon_params(params)
    n = len(df)
    cols = {"signal": np.zeros(n, bool), "score": np.zeros(n), "stop": np.full(n, np.nan), "target": np.full(n, np.nan),
            "rr": np.full(n, np.nan), "clear": np.zeros(n, bool), "sweep": np.zeros(n, bool), "fib": np.zeros(n, bool),
            "bias": np.zeros(n, int), "htf_bias": np.zeros(n, int), "zone_low": np.full(n, np.nan), "zone_high": np.full(n, np.nan)}
    reasons = [""] * n
    if n < 2 * lv.SWING + 30:
        return pd.DataFrame(dict(cols, reason=reasons), index=df.index)
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    vol = df["volume"].to_numpy(float) if "volume" in df else np.full(n, np.nan)
    vavg = pd.Series(vol).rolling(20, min_periods=10).mean().shift(1).to_numpy()
    atr = ind.atr(df, 14).to_numpy(float)
    sma50 = ind.sma(df["close"].astype(float), 50).to_numpy(float)
    rsi = ind.rsi(df["close"].astype(float), 14).to_numpy(float)
    sw = lv.swings(df)
    bias = _bias_series(df, sw)
    hbias = _htf_bias(df) if p["htf"] else np.zeros(n, int)
    bull_pat = pat.detect(df, "any_bullish").to_numpy(bool)
    cols["bias"], cols["htf_bias"] = bias, hbias

    by_known: dict[int, list[lv.Swing]] = {}
    for s in sw:
        by_known.setdefault(s.known, []).append(s)
    known_at = sorted(by_known)
    known_h: list[lv.Swing] = []
    known_l: list[lv.Swing] = []
    pending: list[tuple[lv.Swing, float]] = []
    dzones: list[DZone] = []
    engine_zones: list[lv.Zone] = []
    last_build = -10_000
    flipped: set[int] = set()
    last_h = None
    traded_until = -1  # one trade per retest: wait until price leaves the area again

    for t in range(n):
        a = atr[t]
        if not a == a or a <= 0:
            continue
        new_swing = False
        for s in by_known.get(t, []):
            new_swing = True
            if s.kind == "H":
                known_h.append(s)
                last_h = s
            else:
                known_l.append(s)
                prior = [x for x in known_h if x.i < s.i]
                pending.append((s, prior[-1].price if prior else np.inf))
        # the zone engine's view (rebuilt when a swing is confirmed, as on the chart)
        if new_swing or t - last_build >= REBUILD_EVERY:
            engine_zones = [z for z in lv.zones_at(df, upto=t, sw=sw, atr=atr) if z.score >= 3.0]
            last_build = t
        # demand zones: where a strong move that broke structure started
        still = []
        for s, prior_high in pending:
            if t - s.i > MOVE_BARS:
                continue
            top = h[s.i + 1:t + 1].max() if t > s.i else s.price
            a0 = atr[s.i] if atr[s.i] == atr[s.i] and atr[s.i] > 0 else a
            if top - s.price >= STRONG_ATR * a0 and top > prior_high:
                hi = min(s.price + ZONE_ATR * a0, max(max(o[s.i], c[s.i]), s.price + 0.25 * a0))
                if not any(z.low <= hi and s.price <= z.high for z in dzones):
                    dzones.append(DZone(s.price, hi, "origin", s.i, t, top, meta={"move_atr": round((top - s.price) / a0, 1)}))
            else:
                still.append((s, prior_high))
        pending = still
        # a resistance broken by a big candle becomes support
        if last_h is not None and last_h.i not in flipped and c[t] > last_h.price \
                and (c[t] - o[t] >= FLIP_BODY_ATR * a or c[t] >= last_h.price + FLIP_CLOSE_ATR * a):
            flipped.add(last_h.i)
            lvl = last_h.price
            if not any(z.low <= lvl and lvl - 0.5 * a <= z.high for z in dzones):
                dzones.append(DZone(lvl - 0.5 * a, lvl, "flip", t, t, h[t]))
        dzones = [z for z in dzones if c[t] >= z.low and t - z.created <= ZONE_LIFETIME]
        for z in dzones:
            z.top_since = max(z.top_since, h[t]) if z.created < t else z.top_since
        if t <= traded_until or bias[t] != 1:
            continue  # bearish structure: no new buys
        recent_high = h[max(0, t - 20):t].max() if t > 0 else h[t]
        # candidate areas price is testing now (low inside, close above the bottom), approached from above
        cands = []
        for z in engine_zones:
            if l[t] <= z.high and c[t] > z.low and recent_high >= z.high + LEFT_ATR * a and z.lows >= 1:
                cands.append({"low": z.low, "high": z.high, "pts": min(25.0, z.score / 10 * 25), "touches": z.touches,
                              "what": f"support zone {z.low:.2f}–{z.high:.2f} ({z.touches} touches"
                                      + (", old resistance" if z.flipped else "") + (", weekly level" if z.htf else "") + ")"})
        for z in dzones:
            if z.created < t and l[t] <= z.high and c[t] > z.low and z.top_since >= z.high + LEFT_ATR * a:
                pts = 15 + min(10.0, (z.meta.get("move_atr", 2) - 2) * 5) if z.kind == "origin" else 20.0
                what = (f"demand zone {z.low:.2f}–{z.high:.2f} where a {z.meta.get('move_atr')}×ATR move began" if z.kind == "origin"
                        else f"broken resistance {z.high:.2f} now support")
                cands.append({"low": z.low, "high": z.high, "pts": pts, "touches": 0, "what": what})
        if not cands:
            continue
        cands.sort(key=lambda x: -x["pts"])
        best = cands[0]
        overlap = [x for x in cands[1:] if x["low"] <= best["high"] and best["low"] <= x["high"]]
        zone_pts = min(25.0, best["pts"] + (3 if overlap else 0))
        zlow, zhigh = min([best["low"]] + [x["low"] for x in overlap]), max([best["high"]] + [x["high"] for x in overlap])
        # confirmation
        rng = h[t] - l[t]
        strong_close = c[t] > o[t] and rng > 0 and c[t] >= l[t] + 0.5 * rng
        if bull_pat[t]:
            conf_pts, conf = 20, "bullish reversal pattern"
        elif strong_close:
            conf_pts, conf = 12, "strong bullish close"
        else:
            continue
        # liquidity: a dip below the zone or below equal lows, then a close back above
        recent = [s_.price for s_ in known_l if s_.i >= t - lv.LOOKBACK]
        pools = [min(x, y) for i1, x in enumerate(recent) for y in recent[i1 + 1:] if abs(x - y) <= EQUAL_ATR * a]
        sweep = l[t] < zlow or any(l[t] < pv < c[t] for pv in pools if pv >= c[t] - 2 * a)
        if best["touches"] >= MAX_TOUCHES and not sweep:
            continue
        if p["require_sweep"] and not sweep:
            continue
        vratio = vol[t] / vavg[t] if vavg[t] == vavg[t] and vavg[t] > 0 else None
        vol_pts = 10 if vratio is not None and vratio >= 1.2 else 5 if vratio is not None and vratio >= 1.0 else 0
        if p["require_volume"] and vol_pts < 10:
            continue
        fib = False
        if known_l and known_l[-1].i < t:
            leg_low = known_l[-1]
            leg_high = h[leg_low.i:t].max()
            span = leg_high - leg_low.price
            fib = span > a and leg_high - 0.786 * span <= l[t] <= leg_high - 0.5 * span
        if p["require_fib"] and not fib:
            continue
        trend_pts = 15 + (5 if sma50[t] == sma50[t] and c[t] > sma50[t] else 0)
        if p["htf"]:
            if hbias[t] == -1:
                continue  # the higher timeframe is bearish
            trend_pts += 5 if hbias[t] == 1 else 0
        else:
            trend_pts += 2.5
        rsi_ok = rsi[t] == rsi[t] and 35 <= rsi[t] <= 65
        score = trend_pts + zone_pts + conf_pts + vol_pts + (10 if sweep else 0) + (5 if fib else 0) + (5 if rsi_ok else 0)
        if score < p["min_score"]:
            continue
        stop = min(zlow, l[t]) - 0.1 * a
        entry = c[t]
        risk = entry - stop
        if risk <= 0:
            continue
        res = _resistance_target(df, t, entry, sw, atr)
        if res is None:
            room_r, clear, zone_target = CLEAR_SKY_R, True, entry + CLEAR_SKY_R * risk
        else:
            zone_target = res - 0.05 * a
            room_r = (zone_target - entry) / risk
            clear = room_r >= CLEAR_R
        if room_r < p["min_rr"]:
            continue
        target = zone_target if p["target_mode"] == "zone" else entry + p["min_rr"] * risk
        traded_until = t + 3  # no second entry on the same retest
        cols["signal"][t], cols["score"][t] = True, round(score, 1)
        cols["stop"][t], cols["target"][t] = round(stop, 2), round(target, 2)
        cols["rr"][t] = round((target - entry) / risk, 2)
        cols["clear"][t], cols["sweep"][t], cols["fib"][t] = clear, sweep, fib
        cols["zone_low"][t], cols["zone_high"][t] = round(zlow, 2), round(zhigh, 2)
        why = [f"score {score:.0f}/100", f"trend {trend_pts:g}/25 (bullish structure"
               + (", above 50-candle average" if trend_pts >= 20 or (not p['htf'] and trend_pts >= 17.5) else "")
               + (", higher timeframe agrees" if p["htf"] and hbias[t] == 1 else "") + ")",
               f"zone {zone_pts:.0f}/25: {best['what']}" + (" + overlapping zone" if overlap else ""),
               f"confirmation {conf_pts}/20: {conf}"]
        if vol_pts:
            why.append(f"volume {vol_pts}/10 ({vratio:.1f}× average)")
        if sweep:
            why.append("liquidity 10/10: dipped below the lows and closed back above")
        if fib:
            why.append("Fibonacci 0.5–0.786 pullback")
        why.append("open space to the next resistance" if clear else f"room to the next resistance 1:{room_r:.1f}")
        reasons[t] = "; ".join(why)
    out = pd.DataFrame(dict(cols, reason=reasons), index=df.index)
    price, a_last = c[-1], atr[-1] if atr[-1] == atr[-1] else 0
    waiting = [{"low": round(float(z.low), 2), "high": round(float(z.high), 2), "kind": "zone engine", "touches": z.touches,
                "distance_pct": round(float(z.high / price - 1) * 100, 2)} for z in engine_zones if z.high < price and z.lows >= 1]
    waiting += [{"low": round(float(z.low), 2), "high": round(float(z.high), 2), "kind": z.kind, "touches": 0,
                 "distance_pct": round(float(z.high / price - 1) * 100, 2)} for z in dzones if z.high < price and z.top_since >= z.high + LEFT_ATR * a_last]
    out.attrs["zones"] = sorted(waiting, key=lambda w: -w["high"])[:3]
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
    bias = {1: "bullish", -1: "bearish (no new buys)", 0: "not set yet"}
    out = {"available": True, "bias": bias[int(last["bias"])], "htf_bias": bias[int(last["htf_bias"])] if p["htf"] else None,
           "fired_today": bool(last["signal"]), "waiting_zones": sig.attrs.get("zones", []),
           "recent": [{"ts": i.isoformat(), "score": float(r["score"]), "stop": float(r["stop"]), "target": float(r["target"]),
                       "rr": float(r["rr"]), "why": r["reason"]}
                      for i, r in recent.iterrows()]}
    if last["signal"]:
        out.update({"score": float(last["score"]), "stop": float(last["stop"]), "target": float(last["target"]), "rr": float(last["rr"]),
                    "why": last["reason"]})
    out["min_score"] = p["min_score"]
    return out
