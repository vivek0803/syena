"""LIVE gate per strategy (spec 15 + 17H). A strategy can trade real money only if:
(a) its backtest/replay passed thresholds, (b) it has enough paper trades over enough days,
(c) its paper results are within tolerance of its backtest/replay results. Thresholds live in Settings.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.execution.checks import Check
from syena.market.calendar import to_ist
from syena.models import Algo, Trade, TradeStatus


def paper_stats(db: Session, algo_id: int) -> dict:
    trades = list(db.scalars(select(Trade).where(
        Trade.algo_id == algo_id, Trade.mode == "PAPER", Trade.status == TradeStatus.CLOSED)))
    days = {to_ist(t.created_at).date() for t in trades}
    wins = sum(1 for t in trades if (t.profit_loss or 0) > 0)
    return {
        "trades": len(trades),
        "days": len(days),
        "win_rate": wins / len(trades) if trades else None,
        "pnl": round(sum(t.profit_loss or 0 for t in trades), 2),
    }


def evaluate(db: Session, algo: Algo) -> list[Check]:
    g = settings_store.get(db, "live_gate")
    bt = algo.backtest_result or {}
    m = bt.get("metrics", {})
    ps = paper_stats(db, algo.id)
    checks = [
        Check("backtest_trades", m.get("trades", 0) >= g["min_backtest_trades"],
              f"{m.get('trades', 0)} backtest/replay trades (min {g['min_backtest_trades']})"),
        Check("backtest_win_rate", (m.get("win_rate") or 0) >= g["min_backtest_win_rate"],
              f"win rate {m.get('win_rate')} (min {g['min_backtest_win_rate']})"),
        Check("backtest_sharpe", (m.get("sharpe") or 0) >= g["min_backtest_sharpe"],
              f"Sharpe {m.get('sharpe')} (min {g['min_backtest_sharpe']})"),
        Check("backtest_drawdown", (m.get("max_drawdown_pct") or 999) <= g["max_backtest_drawdown_pct"],
              f"max drawdown {m.get('max_drawdown_pct')}% (max {g['max_backtest_drawdown_pct']}%)"),
        Check("paper_trades", ps["trades"] >= g["min_paper_trades"],
              f"{ps['trades']} closed paper trades (min {g['min_paper_trades']})", ps),
        Check("paper_days", ps["days"] >= g["min_paper_days"], f"{ps['days']} paper trading days (min {g['min_paper_days']})"),
    ]
    if ps["win_rate"] is not None and m.get("win_rate") is not None:
        gap = abs(ps["win_rate"] - m["win_rate"])
        checks.append(Check("paper_vs_backtest", gap <= g["paper_vs_replay_tolerance"],
                            f"paper win rate {ps['win_rate']:.2f} vs backtest {m['win_rate']:.2f} (gap {gap:.2f}, max {g['paper_vs_replay_tolerance']})"))
    else:
        checks.append(Check("paper_vs_backtest", False, "not enough paper results to compare with backtest"))
    return checks
