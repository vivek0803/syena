"""Pattern lab: which candlestick patterns actually made money on NSE stocks, and in which setting?

Every bullish pattern is tested alone and with filters (at a support zone, in an uptrend above the 200-day average,
on above-average volume) across a whole index over several years of daily candles. Each test uses Syena's own
backtester: buy at the next day's open, stop 1.5x ATR, target 3x ATR, out after 10 trading days at the latest,
Zerodha charges and slippage included, no look-ahead. Results are pooled across all stocks.

A combination is marked "works" only when it has enough trades, a positive average result after costs, a profit
factor above 1.2 and stays positive in BOTH the earlier 70% of the period and the latest 30% (so it isn't a
pattern that only worked years ago). Winners can be turned into algos in one click.
"""
import logging
import threading
from datetime import timedelta

from sqlalchemy.orm import Session

from syena.algos import backtest
from syena.algos.patterns import PATTERNS
from syena.algos.rules import canon_rule, describe
from syena.db import utcnow

log = logging.getLogger("syena.patternlab")

STATE_KEY = "_pattern_lab"
PATTERNS_TESTED = [p for p, (_, d, _c) in PATTERNS.items() if d == "bullish" and p not in ("gap_up", "any_bullish")] + ["any_bullish"]


def _cmp(left, cmp, right):
    return {"op": "compare", "left": left, "cmp": cmp, "right": right}


PRICE = lambda f: {"kind": "price", "field": f}  # noqa: E731
IND = lambda name, **p: {"kind": "indicator", "name": name, "params": p}  # noqa: E731
FILTERS = {
    "alone": ("on its own", []),
    "support": ("at a support zone", [_cmp(PRICE("low"), "<=", IND("support_zone")), _cmp(PRICE("close"), ">", IND("support_zone"))]),
    "uptrend": ("in an uptrend (close above 200-day average)", [_cmp(PRICE("close"), ">", IND("sma", n=200))]),
    "volume": ("on above-average volume", [_cmp(PRICE("volume"), ">", IND("volume_sma", n=20))]),
    "support_uptrend": ("at support, in an uptrend", None),
    "all": ("at support, in an uptrend, on above-average volume", None),
}
FILTERS["support_uptrend"] = (FILTERS["support_uptrend"][0], FILTERS["support"][1] + FILTERS["uptrend"][1])
FILTERS["all"] = (FILTERS["all"][0], FILTERS["support"][1] + FILTERS["uptrend"][1] + FILTERS["volume"][1])

RISK = {"stop_loss": {"type": "atr", "mult": 1.5, "n": 14}, "target": {"type": "atr", "mult": 3.0, "n": 14}}
MAX_HOLD_DAYS = 10
MIN_TRADES = 40
SPLIT = 0.7

_lock = threading.Lock()
_progress: dict = {}


def rule_for(pattern: str, filt: str) -> dict:
    node = {"op": "pattern", "name": pattern}
    extra = FILTERS[filt][1]
    entry = {"op": "and", "args": [node, *extra]} if extra else node
    return canon_rule({"entry": entry, "exit": None, "side": "BUY", **RISK})


def english(pattern: str, filt: str) -> str:
    return f"Buy the next morning when a {PATTERNS[pattern][0].lower()} forms {FILTERS[filt][0]}; stop 1.5×ATR, target 3×ATR, max {MAX_HOLD_DAYS} days"


def _stats(rets: list[float]) -> dict:
    n = len(rets)
    if not n:
        return {"trades": 0}
    wins = [r for r in rets if r > 0]
    gross_win, gross_loss = sum(wins), -sum(r for r in rets if r <= 0)
    return {"trades": n, "win_rate": round(len(wins) / n * 100, 1), "avg_return_pct": round(sum(rets) / n, 3),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "median_return_pct": round(sorted(rets)[n // 2], 3)}


def evaluate_combo(frames: dict, pattern: str, filt: str, cfg: backtest.BacktestConfig, split_ts) -> dict:
    rule = rule_for(pattern, filt)
    early, late = [], []
    stocks_with_trades = 0
    for sym, (df, cache) in frames.items():
        try:
            res = backtest.run(df, rule, cfg, symbol=sym, cache=cache)
        except Exception as e:  # one bad stock must not stop the lab
            log.info("pattern lab backtest failed", extra={"symbol": sym, "error": str(e)[:120]})
            continue
        if res["trades"]:
            stocks_with_trades += 1
        for t in res["trades"]:
            (early if t["entry_ts"] < split_ts else late).append(t["return_pct"])
    allr = early + late
    s = _stats(allr)
    s.update({"pattern": pattern, "pattern_label": PATTERNS[pattern][0], "filter": filt, "filter_label": FILTERS[filt][0],
              "stocks": stocks_with_trades, "early": _stats(early), "late": _stats(late)})
    s["works"] = bool(s["trades"] >= MIN_TRADES and s["avg_return_pct"] > 0.25 and (s["profit_factor"] or 0) > 1.2
                      and s["early"].get("avg_return_pct", -1) > 0 and s["late"].get("trades", 0) >= 10
                      and s["late"].get("avg_return_pct", -1) > 0)
    return s


def run(db: Session, md, universe: str = "nifty200", years: int = 5, symbols: list[str] | None = None) -> dict:
    """Download (or reuse) daily candles for the index, test every pattern x filter, save and return the results."""
    from syena.algos.service import backtest_config
    from syena.models import Style
    from syena.screener import lists

    syms = symbols or lists.index_members(db, universe)
    end = utcnow()
    start = end - timedelta(days=int(years * 365.25) + 300)  # + warm-up for the 200-day average
    frames, failed = {}, []
    _progress.update({"stage": "downloading prices", "done": 0, "total": len(syms)})
    for i, s in enumerate(syms):
        try:
            df = md.candles(s, "day", start, end)
            if df is not None and len(df) > 260:
                frames[s] = (df, {})
        except Exception as e:
            failed.append(s)
            log.info("pattern lab: no candles", extra={"symbol": s, "error": str(e)[:120]})
        _progress["done"] = i + 1
    if not frames:
        raise ValueError("no price history could be loaded for this index")
    cfg = backtest_config(db, Style.SWING)
    cfg.max_hold_days = MAX_HOLD_DAYS
    cfg.starting_capital = 1e12  # each signal is judged on its own, not limited by cash
    first = min(df.index[260] for df, _ in frames.values())
    last = max(df.index[-1] for df, _ in frames.values())
    split_ts = (first + (last - first) * SPLIT).isoformat()
    combos = [(p, f) for p in PATTERNS_TESTED for f in FILTERS]
    _progress.update({"stage": "testing patterns", "done": 0, "total": len(combos)})
    rows = []
    for k, (p, f) in enumerate(combos):
        rows.append(evaluate_combo(frames, p, f, cfg, split_ts))
        _progress["done"] = k + 1
    rows.sort(key=lambda r: (not r["works"], r["trades"] < MIN_TRADES, -(r.get("avg_return_pct") or -99) if r["trades"] else 99))
    out = {"at": utcnow().isoformat(), "universe": universe, "years": years, "stocks": len(frames), "failed": failed,
           "period": {"from": str(first.date()), "to": str(last.date()), "split": split_ts[:10]},
           "method": {"entry": "next day's open", "stop": "1.5×ATR", "target": "3×ATR", "max_hold_days": MAX_HOLD_DAYS,
                      "costs": "Zerodha charges + slippage", "works_if": f"≥{MIN_TRADES} trades, avg > +0.25% after costs, "
                      "profit factor > 1.2, positive in both the earlier 70% and the latest 30% of the period"},
           "results": rows, "winners": sum(r["works"] for r in rows)}
    settings_store_set(db, out)
    return out


def settings_store_set(db: Session, value: dict) -> None:
    from syena.models import AppSetting

    row = db.get(AppSetting, STATE_KEY)
    if row is None:
        db.add(AppSetting(key=STATE_KEY, value=value, updated_at=utcnow()))
    else:
        row.value, row.updated_at = value, utcnow()
    db.commit()


def latest(db: Session) -> dict | None:
    from syena.models import AppSetting

    row = db.get(AppSetting, STATE_KEY)
    return row.value if row else None


def progress() -> dict:
    return dict(_progress)


def create_algo(db: Session, pattern: str, filt: str, symbols: list[str]):
    """A DRAFT daily algo for a winning combination (review, backtest and start it on the Algos page as usual)."""
    from syena.models import Algo, Style

    if pattern not in PATTERNS_TESTED or filt not in FILTERS:
        raise ValueError("unknown pattern or filter")
    rule = rule_for(pattern, filt)
    a = Algo(name=f"{PATTERNS[pattern][0]} {FILTERS[filt][0]}"[:120], english_text=english(pattern, filt), style=Style.SWING,
             run_mode="PAPER", symbols=symbols, timeframe="day", parsed_rule=rule, parser_source="pattern_lab", status="DRAFT")
    db.add(a)
    db.commit()
    return a, describe(rule["entry"])
