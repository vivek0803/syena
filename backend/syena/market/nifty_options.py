"""NIFTY daily options strategy (paper): at most one intraday trade a day, a call or a put on NIFTY.

The plan, every trading day:
  1. Bias from the daily chart: market structure (a close above the last swing high = bullish, below the last swing
     low = bearish, kept until the opposite break) and the close against its 20-day EMA. Both agree -> that bias;
     otherwise neutral (both directions allowed, the breakout decides).
  2. Opening range: the high and low of the first 15 minutes (09:15-09:30).
  3. Entry window 09:30-11:30: a 5-minute candle CLOSING above the opening-range high (and the bias isn't bearish)
     -> buy the at-the-money call; closing below the low (bias not bullish) -> buy the at-the-money put.
     The option chain must not be strongly against it: a call needs put-call ratio >= 0.7, a put needs <= 1.4.
     A range wider than 1.2% of the price is skipped (the move already happened at the open).
  4. Contract: nearest weekly expiry with at least `min_days_to_expiry` days left (expiry-day contracts are avoided).
  5. Exit: premium stop (-25%) / target (+50%), the options trailing stop, and square-off at 15:10 regardless.

The backtest replays the same rules on NIFTY's own 5-minute candles (option prices aren't available historically):
it reports the index move captured per trade, and an estimated option result per lot from an ATM delta of 0.5
minus premium decay and charges. Treat that as a guide to the signal's quality, not the exact option P&L.
"""
import logging
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.algos import indicators as ind
from syena.algos import levels as lv
from syena.db import utcnow
from syena.market import calendar
from syena.models import Algo, AppSetting, Style, Trade

log = logging.getLogger("syena.nifty")

UNDERLYING = "NIFTY"
STATE_KEY = "_nifty_options"
DEFAULTS = {"enabled": True, "lots": 1, "stop_pct": 25.0, "target_pct": 50.0, "min_days_to_expiry": 1,
            "entry_start": "09:30", "entry_end": "11:30", "max_range_pct": 1.2, "pcr_min_call": 0.7, "pcr_max_put": 1.4}


def config(db: Session) -> dict:
    return {**DEFAULTS, **(settings_store.get(db, "nifty_options") or {})}


def strategy_algo(db: Session) -> Algo:
    a = db.scalar(select(Algo).where(Algo.kind == "nifty_opt"))
    if a is None:
        a = Algo(name="NIFTY daily options", kind="nifty_opt", instrument="OPT", style=Style.INTRADAY, run_mode="PAPER",
                 status="RUNNING", symbols=[UNDERLYING], timeframe="5minute",
                 english_text="Daily bias + opening-range breakout on NIFTY: buy an ATM call above the range, an ATM put below (paper, intraday)")
        db.add(a)
        db.commit()
    return a


def daily_bias(daily: pd.DataFrame) -> tuple[str, list[str]]:
    """bullish | bearish | neutral from the daily candles up to (not including) today."""
    if daily is None or len(daily) < 30:
        return "neutral", ["not enough daily history"]
    from syena.algos.zonetrade import _bias_series

    st = int(_bias_series(daily, lv.swings(daily))[-1])
    ema = float(ind.ema(daily["close"].astype(float), 20).iloc[-1])
    close = float(daily["close"].iloc[-1])
    why = [f"structure {'bullish' if st == 1 else 'bearish' if st == -1 else 'unset'}",
           f"close {close:,.0f} {'above' if close > ema else 'below'} 20-day EMA {ema:,.0f}"]
    if st == 1 and close > ema:
        return "bullish", why
    if st == -1 and close < ema:
        return "bearish", why
    return "neutral", why


def _ist(df: pd.DataFrame) -> pd.DataFrame:
    return df.set_axis(df.index.tz_convert(calendar.IST)) if df.index.tz is not None else df


def day_signal(bias: str, bars: pd.DataFrame, cfg: dict, pcr: float | None = None) -> dict:
    """The day's decision from its 5-minute candles so far (IST index). Pure: used live and in the backtest."""
    out = {"bias": bias, "direction": None, "or_high": None, "or_low": None, "reasons": []}
    if bars is None or bars.empty:
        out["reasons"].append("no candles yet")
        return out
    t0 = time(9, 15)
    rng = bars[(bars.index.time >= t0) & (bars.index.time < time(9, 30))]
    if len(rng) < 2:
        out["reasons"].append("opening range not complete")
        return out
    hi, lo = float(rng["high"].max()), float(rng["low"].min())
    out.update(or_high=hi, or_low=lo)
    width = (hi - lo) / lo * 100
    if width > float(cfg["max_range_pct"]):
        out["reasons"].append(f"opening range {width:.2f}% wide (> {cfg['max_range_pct']}%): the move happened at the open, no trade")
        return out
    start, end = (calendar.parse_hhmm(cfg["entry_start"]), calendar.parse_hhmm(cfg["entry_end"]))
    window = bars[(bars.index.time >= start) & (bars.index.time < end)]
    for ts, r in window.iterrows():
        if r["close"] > hi and bias != "bearish":
            if pcr is not None and pcr < float(cfg["pcr_min_call"]):
                out["reasons"].append(f"breakout up at {ts:%H:%M}, but put-call ratio {pcr} < {cfg['pcr_min_call']} (call writers heavy): skipped")
                return out
            out.update(direction="BUY", at=ts.isoformat(), price=float(r["close"]))
            out["reasons"].append(f"5-min close {r['close']:,.1f} above the opening-range high {hi:,.1f} at {ts:%H:%M} (bias {bias})")
            return out
        if r["close"] < lo and bias != "bullish":
            if pcr is not None and pcr > float(cfg["pcr_max_put"]):
                out["reasons"].append(f"breakdown at {ts:%H:%M}, but put-call ratio {pcr} > {cfg['pcr_max_put']} (put writers heavy): skipped")
                return out
            out.update(direction="SELL", at=ts.isoformat(), price=float(r["close"]))
            out["reasons"].append(f"5-min close {r['close']:,.1f} below the opening-range low {lo:,.1f} at {ts:%H:%M} (bias {bias})")
            return out
    out["reasons"].append(f"waiting: no close outside {lo:,.1f}–{hi:,.1f} yet" + ("" if bias == "neutral" else f" in the {bias} direction"))
    return out


def _save_state(db: Session, day: str, state: dict) -> None:
    row = db.get(AppSetting, STATE_KEY)
    val = dict((row.value or {}) if row else {})
    days = dict(val.get("days") or {})
    days[day] = state
    val["days"] = dict(sorted(days.items())[-30:])
    if row is None:
        db.add(AppSetting(key=STATE_KEY, value=val, updated_at=utcnow()))
    else:
        row.value, row.updated_at = val, utcnow()
    db.commit()


def get_state(db: Session) -> dict:
    row = db.get(AppSetting, STATE_KEY)
    return (row.value or {}) if row else {}


def run(db: Session, md, now: datetime | None = None, broker_factory=None) -> dict:
    """Called every 5 minutes in the entry window. Places at most one trade a day."""
    from syena.market import options as op
    from syena.market.options_trade import enter_option

    now = now or utcnow()
    cfg = config(db)
    ist = calendar.to_ist(now)
    day = ist.date().isoformat()
    algo = strategy_algo(db)
    if not cfg.get("enabled", True):
        return {"skipped": "turned off"}
    start_day = datetime.combine(ist.date(), time(0, 0), calendar.IST)
    if db.scalar(select(Trade.id).where(Trade.algo_id == algo.id, Trade.created_at >= start_day).limit(1)):
        return {"skipped": "already traded today"}
    daily = md.candles(UNDERLYING, "day", now - timedelta(days=200), now)
    daily = daily[[d.date() < ist.date() for d in _ist(daily).index]] if len(daily) else daily
    bias, why = daily_bias(daily)
    bars = _ist(md.candles(UNDERLYING, "5minute", start_day, now))
    bars = bars[[d.date() == ist.date() for d in bars.index]]
    if len(bars) and ist < bars.index[-1].to_pydatetime() + timedelta(minutes=5):
        bars = bars.iloc[:-1]  # the candle still forming
    pcr = None
    try:
        op.ensure_contracts(db, md)
        ch = op.chain(db, md, UNDERLYING, None, strikes=10, now=now)
        pcr = ch["summary"]["pcr"]
    except Exception as e:
        log.info("nifty chain unavailable", extra={"error": str(e)[:150]})
    sig = day_signal(bias, bars, cfg, pcr)
    state = {"at": now.isoformat(), "bias": bias, "bias_why": why, "pcr": pcr, **{k: sig.get(k) for k in ("direction", "or_high", "or_low", "at", "price")},
             "reasons": sig["reasons"], "decision_id": None}
    if sig["direction"]:
        from syena.execution.checks import Check

        d = enter_option(db, md, UNDERLYING, sig["direction"], source="algo", algo_id=algo.id, confidence=None,
                         reasoning={"bias": bias, "bias_why": why, "signal": sig["reasons"], "pcr": pcr},
                         inputs={"or_high": sig["or_high"], "or_low": sig["or_low"], "nifty": sig.get("price")},
                         extra_checks=[Check("nifty_signal", True, "; ".join(sig["reasons"]))],
                         idempotency_key=f"nifty-{day}", lots=int(cfg["lots"]), stop_pct=float(cfg["stop_pct"]),
                         target_pct=float(cfg["target_pct"]), style=Style.INTRADAY, min_days=float(cfg["min_days_to_expiry"]),
                         now=now, broker_factory=broker_factory)
        state["decision_id"], state["decision"] = d.id, d.summary
    _save_state(db, day, state)
    return state


# ---------------------------------------------------------------- backtest of the signal
def backtest(daily: pd.DataFrame, five: pd.DataFrame, cfg: dict | None = None, lot_size: int = 75,
             premium_pct: float = 0.45) -> dict:
    """Replay the rules day by day on NIFTY candles. Each trade is held from the signal candle's close to the
    earlier of: the index crossing back through the far side of the opening range (the option's stop is roughly
    there), or 15:10. Option result per lot estimated as delta 0.5 × index points × lot - 1.5% premium decay - ₹60."""
    cfg = {**DEFAULTS, **(cfg or {})}
    d_ist, f_ist = _ist(daily), _ist(five)
    days = sorted({ts.date() for ts in f_ist.index})
    trades, skipped = [], 0
    for dday in days:
        hist = daily[[x.date() < dday for x in d_ist.index]]
        bias, _ = daily_bias(hist)
        bars = f_ist[[x.date() == dday for x in f_ist.index]]
        sig = day_signal(bias, bars, cfg)
        if not sig["direction"]:
            skipped += 1
            continue
        entry_ts = pd.Timestamp(sig["at"])
        after = bars[bars.index > entry_ts]
        entry = sig["price"]
        stop = sig["or_low"] if sig["direction"] == "BUY" else sig["or_high"]
        exit_px, reason = None, "square_off"
        for ts, r in after.iterrows():
            if ts.time() >= time(15, 10):
                exit_px = float(r["open"])
                break
            if (sig["direction"] == "BUY" and r["low"] <= stop) or (sig["direction"] == "SELL" and r["high"] >= stop):
                exit_px, reason = stop, "stop"
                break
        if exit_px is None:
            exit_px = float(after["close"].iloc[-1]) if len(after) else entry
        pts = (exit_px - entry) if sig["direction"] == "BUY" else (entry - exit_px)
        premium = entry * premium_pct / 100  # an ATM weekly premium is roughly 0.3-0.6% of the index
        est = 0.5 * pts * lot_size - 0.015 * premium * lot_size - 60
        trades.append({"day": dday.isoformat(), "side": "call" if sig["direction"] == "BUY" else "put", "bias": bias,
                       "entry": round(entry, 1), "exit": round(exit_px, 1), "points": round(pts, 1), "reason": reason,
                       "est_option_pnl": round(est, 0)})
    pts = [t["points"] for t in trades]
    est = [t["est_option_pnl"] for t in trades]
    wins = [x for x in est if x > 0]
    return {"days": len(days), "trades": len(trades), "no_trade_days": skipped,
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "avg_points": round(float(np.mean(pts)), 1) if pts else None, "total_points": round(float(np.sum(pts)), 1) if pts else 0,
            "est_total_pnl": round(float(np.sum(est)), 0) if est else 0,
            "profit_factor": round(sum(wins) / -sum(x for x in est if x <= 0), 2) if any(x <= 0 for x in est) and wins else None,
            "lot_size": lot_size, "list": trades[-60:]}
