"""Trailing stop-loss: the stop follows the price up (down for shorts) and never moves back.

Shares (news and algo trades): once the trade is `activate_r` × its initial risk in profit (risk = entry - stop), the
stop moves to break-even, and from then on sits `distance_r` × that risk below the best price since entry.
Options (bought calls and puts, any source): once the premium is up `activate_pct`%, the stop trails `trail_pct`%
below the highest premium since entry.

The same rule runs in live / paper monitoring (every minute on live prices) and in the backtester (updated after
each closed candle, so it never uses a candle's own high to protect that candle: no look-ahead).
Manual long-term investments are left alone: you decide when to sell those.
"""
from datetime import datetime

from syena import settings_store
from syena.models import Style, Trade

DEFAULTS = {"enabled": True, "equity": {"activate_r": 1.0, "distance_r": 1.0},
            "options": {"activate_pct": 20.0, "trail_pct": 25.0}}


def config(db) -> dict:
    cfg = settings_store.get(db, "trailing")
    return {**DEFAULTS, **cfg, "equity": {**DEFAULTS["equity"], **(cfg.get("equity") or {})},
            "options": {**DEFAULTS["options"], **(cfg.get("options") or {})}}


def eligible(t: Trade) -> bool:
    if (t.instrument or "EQ") == "OPT":
        return True
    return t.source in ("news", "algo") and t.style != Style.LONG_TERM


def init_state(t: Trade, cfg: dict) -> dict | None:
    """Work out the trailing rule for a filled trade (needs its entry price and initial stop)."""
    if not t.entry_price or not t.stop_loss:
        return None
    short = t.side == "SELL"
    if (t.instrument or "EQ") == "OPT":
        o = cfg["options"]
        return {"kind": "pct", "initial_stop": t.stop_loss, "pct": float(o["trail_pct"]), "best": t.entry_price,
                "activate_at": round(t.entry_price * (1 + float(o["activate_pct"]) / 100), 2), "moved": 0}
    risk = abs(t.entry_price - t.stop_loss)
    if risk <= 0:
        return None
    e = cfg["equity"]
    act = t.entry_price - e["activate_r"] * risk if short else t.entry_price + e["activate_r"] * risk
    return {"kind": "r", "initial_stop": t.stop_loss, "dist": round(float(e["distance_r"]) * risk, 4), "best": t.entry_price,
            "activate_at": round(act, 2), "moved": 0}


def new_stop(state: dict, entry: float, stop: float, price: float, short: bool) -> tuple[float, dict]:
    """Given the latest price, the (possibly) raised stop and the updated state. Pure: used live and in backtests."""
    st = dict(state)
    st["best"] = min(st["best"], price) if short else max(st["best"], price)
    reached = st["best"] <= st["activate_at"] if short else st["best"] >= st["activate_at"]
    if not reached:
        return stop, st
    if st["kind"] == "pct":
        cand = st["best"] * (1 - st["pct"] / 100)
    else:
        trail = st["best"] + st["dist"] if short else st["best"] - st["dist"]
        cand = min(trail, entry) if short else max(trail, entry)  # at least break-even once activated
    cand = round(cand, 2)
    better = cand < stop if short else cand > stop
    if better:
        st["moved"] = st.get("moved", 0) + 1
        return cand, st
    return stop, st


def update(db, t: Trade, price: float, now: datetime, cfg: dict | None = None) -> bool:
    """Ratchet one open trade's stop on the latest price. True if the stop moved."""
    cfg = cfg or config(db)
    if not cfg.get("enabled", True) or not eligible(t) or not price:
        return False
    st = t.trail or init_state(t, cfg)
    if st is None:
        return False
    stop, st2 = new_stop(st, t.entry_price, t.stop_loss, price, t.side == "SELL")
    moved = stop != t.stop_loss
    if moved:
        st2["last_moved_at"] = now.isoformat()
        t.stop_loss = stop
    if st2 != t.trail:
        t.trail = st2
    return moved
