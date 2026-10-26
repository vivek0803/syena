"""Bar-by-bar backtester with no lookahead.

- Signals are computed on bar t's close and filled at bar t+1's open (plus slippage).
- Stop/target are checked intrabar on later bars using high/low; if both are touched in the
  same bar the stop is assumed first (conservative). Gaps through a stop fill at the open.
- Sizing (₹ band), Zerodha charges, intraday cutoff/square-off and swing max-hold are applied.
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from syena.algos import indicators as ind
from syena.algos.rules import evaluate
from syena.execution.sizing import size_position
from syena.market.calendar import IST, parse_hhmm
from syena.market.charges import round_trip
from syena.util import jsonable


@dataclass
class BacktestConfig:
    style: str = "SWING"  # INTRADAY | SWING
    min_amount: float = 7000.0
    max_amount: float = 10000.0
    starting_capital: float = 100000.0
    slippage_bps: float = 5.0
    default_stop_atr: float = 2.0
    default_target_atr: float = 4.0
    max_hold_days: int = 20
    entry_cutoff: str = "14:45"
    square_off: str = "15:10"


def risk_levels(rule: dict, entry: float, atr_value: float, cfg: BacktestConfig) -> tuple[float | None, float | None]:
    def level(spec, default_mult, sign):
        if spec is None:
            spec = {"type": "atr", "mult": default_mult}
        if spec["type"] == "pct":
            return entry * (1 + sign * spec["value"] / 100)
        if atr_value is None or math.isnan(atr_value):
            return None
        return entry + sign * spec["mult"] * atr_value

    return level(rule.get("stop_loss"), cfg.default_stop_atr, -1), level(rule.get("target"), cfg.default_target_atr, +1)


def run(df: pd.DataFrame, rule: dict, cfg: BacktestConfig, symbol: str = "", cache: dict | None = None) -> dict:
    df = df.sort_index()
    if len(df) < 5:
        return {"metrics": _metrics([], [], cfg), "trades": [], "equity": [], "skipped": [], "bars": len(df)}
    cache = {} if cache is None else cache  # indicator values shared between rules on the same candles (pattern lab)
    entry_sig = evaluate(df, rule["entry"], cache).to_numpy()
    exit_sig = evaluate(df, rule["exit"], cache).to_numpy() if rule.get("exit") else np.zeros(len(df), bool)
    atr_s = ind.atr(df, (rule.get("stop_loss") or {}).get("n", 14)).to_numpy()
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    times = df.index.tz_convert(IST)
    intraday = cfg.style == "INTRADAY"
    cutoff, sq = parse_hhmm(cfg.entry_cutoff), parse_hhmm(cfg.square_off)
    slip = cfg.slippage_bps / 10_000

    trades, skipped, equity_pts = [], [], []
    cash = cfg.starting_capital
    pos = None  # dict
    pending_entry = pending_exit = False
    for i in range(len(df)):
        t = times[i]
        # 1) fills scheduled from the previous bar's close
        if pending_exit and pos:
            pos["exit_reason"] = pos.get("pending_reason", "exit_rule")
            pos = _close(pos, o[i] * (1 - slip), t, trades, intraday)
            cash += trades[-1]["proceeds"]
            pending_exit = False
        if pending_entry and pos is None:
            pending_entry = False
            px = o[i] * (1 + slip)
            sz = size_position(px, cfg.min_amount, cfg.max_amount)
            if not sz.ok:
                skipped.append({"ts": t.isoformat(), "reason": sz.reason})
            elif intraday and t.time() >= cutoff:
                skipped.append({"ts": t.isoformat(), "reason": "after intraday cutoff"})
            else:
                stop, target = risk_levels(rule, px, atr_s[i - 1] if i > 0 else np.nan, cfg)
                pos = {"entry_ts": t, "entry": px, "qty": sz.qty, "stop": stop, "target": target, "entry_i": i}
                cash -= px * sz.qty
        # 2) intrabar stop / target on an open position
        if pos:
            hit = None
            if pos["stop"] is not None and l[i] <= pos["stop"]:
                hit = ("stop_loss", min(o[i], pos["stop"]) if i > pos["entry_i"] else pos["stop"])
            elif pos["target"] is not None and h[i] >= pos["target"]:
                hit = ("target", max(o[i], pos["target"]) if i > pos["entry_i"] else pos["target"])
            if hit:
                pos["exit_reason"] = hit[0]
                pos = _close(pos, hit[1] * (1 - slip), t, trades, intraday)
                cash += trades[-1]["proceeds"]
                pending_exit = False
        # 3) time-based exits
        if pos:
            last_bar_of_day = i + 1 == len(df) or times[i + 1].date() != t.date()
            if intraday and (t.time() >= sq or last_bar_of_day):
                pos["exit_reason"] = "square_off"
                pos = _close(pos, c[i] * (1 - slip), t, trades, intraday)
                cash += trades[-1]["proceeds"]
            elif not intraday and (t - pos["entry_ts"]).days >= cfg.max_hold_days:
                pos["exit_reason"] = "max_holding_period"
                pos = _close(pos, c[i] * (1 - slip), t, trades, intraday)
                cash += trades[-1]["proceeds"]
        # 4) signals on this bar's close -> act next bar
        if pos and exit_sig[i] and not pending_exit:
            pending_exit, pos["pending_reason"] = True, "exit_rule"
        if pos is None and entry_sig[i] and i + 1 < len(df):
            if not intraday or times[i + 1].date() == t.date():
                pending_entry = True
        mtm = cash + (pos["qty"] * c[i] if pos else 0)
        equity_pts.append((t, mtm))
    if pos:  # close at the end of data
        pos["exit_reason"] = "end_of_data"
        pos = _close(pos, c[-1], times[-1], trades, intraday)
        cash += trades[-1]["proceeds"]
        equity_pts[-1] = (times[-1], cash)
    eq = _equity_with_charges(equity_pts, trades)
    return jsonable({
        "symbol": symbol,
        "metrics": _metrics(trades, eq, cfg),
        "trades": [{k: v for k, v in tr.items() if k != "proceeds"} for tr in trades],
        "skipped": skipped[:200],
        "equity": _downsample(eq, 400),
        "bars": len(df),
        "start": df.index[0].isoformat(),
        "end": df.index[-1].isoformat(),
    })


def _close(pos, px, t, trades, intraday):
    charges = round_trip(pos["qty"], pos["entry"], px, intraday)
    pnl = (px - pos["entry"]) * pos["qty"] - charges
    trades.append({
        "entry_ts": pos["entry_ts"].isoformat(), "exit_ts": t.isoformat(), "entry": round(pos["entry"], 2),
        "exit": round(px, 2), "qty": pos["qty"], "charges": charges, "pnl": round(pnl, 2),
        "return_pct": round(pnl / (pos["entry"] * pos["qty"]) * 100, 3), "exit_reason": pos["exit_reason"],
        "stop": None if pos["stop"] is None else round(pos["stop"], 2),
        "target": None if pos["target"] is None else round(pos["target"], 2),
        "proceeds": px * pos["qty"] - charges,
    })
    return None


def _equity_with_charges(points, trades):
    return [{"ts": t.isoformat(), "equity": round(v, 2)} for t, v in points]


def _downsample(eq, n):
    if len(eq) <= n:
        return eq
    step = math.ceil(len(eq) / n)
    out = eq[::step]
    if out[-1] is not eq[-1]:
        out.append(eq[-1])
    return out


def _metrics(trades: list[dict], equity: list[dict], cfg: BacktestConfig) -> dict:
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = sum(t["pnl"] for t in trades)
    gross_win, gross_loss = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    sharpe = max_dd = None
    if equity:
        s = pd.Series([e["equity"] for e in equity], index=pd.to_datetime([e["ts"] for e in equity]))
        daily = s.groupby(s.index.date).last()
        rets = daily.pct_change().dropna()
        if len(rets) > 1 and rets.std() > 0:
            sharpe = round(float(rets.mean() / rets.std() * math.sqrt(252)), 3)
        peak = s.cummax()
        max_dd = round(float(((s - peak) / peak).min() * -100), 3)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "total_pnl": round(total, 2),
        "return_pct": round(total / cfg.starting_capital * 100, 3),
        "avg_trade": round(total / n, 2) if n else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "total_charges": round(sum(t["charges"] for t in trades), 2),
    }


def combine(results: list[dict], cfg: BacktestConfig) -> dict:
    """Aggregate per-symbol results into one report (trades from all symbols)."""
    trades = sorted([dict(t, symbol=r["symbol"]) for r in results for t in r["trades"]], key=lambda t: t["exit_ts"])
    eq, cash = [], cfg.starting_capital
    for t in trades:
        cash += t["pnl"]
        eq.append({"ts": t["exit_ts"], "equity": round(cash, 2)})
    m = _metrics(trades, eq, cfg)
    return jsonable({"metrics": m, "trades": trades, "equity": _downsample(eq, 400),
            "per_symbol": {r["symbol"]: r["metrics"] for r in results},
            "skipped": [dict(s, symbol=r["symbol"]) for r in results for s in r["skipped"]][:200]})
