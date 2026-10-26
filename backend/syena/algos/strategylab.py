"""Strategy lab: does the zone retest strategy (Umar Punjabi method) make money on NSE stocks, and which version?

Same yardstick as the pattern lab: Syena's own backtester over several years of daily candles for a whole index,
fill at the next day's open, Zerodha charges and slippage included, no look-ahead, results pooled across stocks and
split into the earlier 70% and the latest 30% of the period. A version "works" only with enough trades, a positive
average after costs, profit factor above 1.2 and positive results in both parts.

Versions tested (each changes one thing from his base method):
  base     - his full method: bias + strong zone + confirmation, target the next resistance (hold through clear areas)
  fixed2   - same entries, but always book at 1:2
  fib      - only retests inside the 0.5-0.786 Fibonacci pullback
  sweep    - only retests that sweep liquidity first
  rr3      - only when the next resistance gives at least 1:3
  no_htf   - without the weekly bias filter (shows whether the top-down check earns its keep)
"""
import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from syena.algos import backtest, zonetrade as zt
from syena.algos.patternlab import MIN_TRADES, SPLIT, _stats
from syena.algos.rules import canon_rule, describe_rule
from syena.db import utcnow

log = logging.getLogger("syena.strategylab")

STATE_KEY = "_strategy_lab"
VARIANTS = {
    "base": ("His full method (target the next resistance)", {}),
    "fixed2": ("Same entries, always book at 1:2", {"target_mode": "fixed2"}),
    "fib": ("Only inside the 0.5–0.786 Fibonacci pullback", {"require_fib": True}),
    "sweep": ("Only after a liquidity sweep", {"require_sweep": True}),
    "rr3": ("Only with room of at least 1:3", {"min_rr": 3.0}),
    "no_htf": ("Without the weekly bias filter", {"htf": False}),
}
_progress: dict = {}


def rule_for(variant: str) -> dict:
    params = zt.canon_params(VARIANTS[variant][1])
    return canon_rule({"entry": {"op": "strategy", "name": zt.NAME, "params": params}, "exit": None, "side": "BUY",
                       "stop_loss": {"type": "strategy"}, "target": {"type": "strategy"}})


def evaluate_variant(frames: dict, variant: str, cfg: backtest.BacktestConfig, split_ts: str) -> dict:
    rule = rule_for(variant)
    early, late, rrs = [], [], []
    stocks_with_trades = 0
    reasons: dict[str, int] = {}
    for sym, (df, cache) in frames.items():
        try:
            res = backtest.run(df, rule, cfg, symbol=sym, cache=cache)
        except Exception as e:
            log.info("strategy lab backtest failed", extra={"symbol": sym, "error": str(e)[:120]})
            continue
        if res["trades"]:
            stocks_with_trades += 1
        for t in res["trades"]:
            (early if t["entry_ts"] < split_ts else late).append(t["return_pct"])
            reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
            if t["stop"] and t["target"] and t["entry"] > t["stop"]:
                rrs.append((t["target"] - t["entry"]) / (t["entry"] - t["stop"]))
    s = _stats(early + late)
    s.update({"variant": variant, "label": VARIANTS[variant][0], "stocks": stocks_with_trades,
              "early": _stats(early), "late": _stats(late), "exits": reasons,
              "avg_planned_rr": round(sum(rrs) / len(rrs), 2) if rrs else None})
    s["works"] = bool(s["trades"] >= MIN_TRADES and s["avg_return_pct"] > 0.25 and (s["profit_factor"] or 0) > 1.2
                      and s["early"].get("avg_return_pct", -1) > 0 and s["late"].get("trades", 0) >= 10
                      and s["late"].get("avg_return_pct", -1) > 0)
    return s


def run(db: Session, md, universe: str = "nifty200", years: int = 5, symbols: list[str] | None = None) -> dict:
    from syena.algos.service import backtest_config
    from syena.models import AppSetting, Style
    from syena.screener import lists

    syms = symbols or lists.index_members(db, universe)
    end = utcnow()
    start = end - timedelta(days=int(years * 365.25) + 400)
    frames, failed = {}, []
    _progress.update({"stage": "downloading prices", "done": 0, "total": len(syms)})
    for i, s in enumerate(syms):
        try:
            df = md.candles(s, "day", start, end)
            if df is not None and len(df) > 300:
                frames[s] = (df, {})
        except Exception as e:
            failed.append(s)
            log.info("strategy lab: no candles", extra={"symbol": s, "error": str(e)[:120]})
        _progress["done"] = i + 1
    if not frames:
        raise ValueError("no price history could be loaded for this index")
    cfg = backtest_config(db, Style.SWING)
    cfg.starting_capital = 1e12
    first = min(df.index[260] for df, _ in frames.values())
    last = max(df.index[-1] for df, _ in frames.values())
    split_ts = (first + (last - first) * SPLIT).isoformat()
    _progress.update({"stage": "testing versions", "done": 0, "total": len(VARIANTS)})
    rows = []
    for k, v in enumerate(VARIANTS):
        rows.append(evaluate_variant(frames, v, cfg, split_ts))
        _progress["done"] = k + 1
    out = {"at": utcnow().isoformat(), "universe": universe, "years": years, "stocks": len(frames), "failed": failed,
           "period": {"from": str(first.date()), "to": str(last.date()), "split": split_ts[:10]},
           "method": {"entry": "next day's open after the confirmation candle", "stop": "just beyond the zone / candle low",
                      "target": "next strong resistance (or 1:2 in 'fixed2')", "max_hold_days": cfg.max_hold_days,
                      "costs": "Zerodha charges + slippage",
                      "works_if": f"≥{MIN_TRADES} trades, avg > +0.25% after costs, profit factor > 1.2, positive in both "
                                  "the earlier 70% and the latest 30% of the period"},
           "results": rows, "winners": sum(r["works"] for r in rows)}
    row = db.get(AppSetting, STATE_KEY)
    if row is None:
        db.add(AppSetting(key=STATE_KEY, value=out, updated_at=utcnow()))
    else:
        row.value, row.updated_at = out, utcnow()
    db.commit()
    return out


def latest(db: Session) -> dict | None:
    from syena.models import AppSetting

    row = db.get(AppSetting, STATE_KEY)
    return row.value if row else None


def progress() -> dict:
    return dict(_progress)


def create_algo(db: Session, variant: str, symbols: list[str]):
    """A DRAFT daily swing algo running this version in PAPER (review, backtest and start it on the Algos page)."""
    from syena.models import Algo, Style

    if variant not in VARIANTS:
        raise ValueError("unknown version")
    rule = rule_for(variant)
    a = Algo(name=f"Zone retest – {VARIANTS[variant][0]}"[:120], english_text=describe_rule(rule), style=Style.SWING,
             run_mode="PAPER", symbols=symbols, timeframe="day", parsed_rule=rule, parser_source="strategy_lab", status="DRAFT")
    db.add(a)
    db.commit()
    return a, describe_rule(rule)
