"""Trailing stop-loss: ratchets with the price, never back; break-even first; options trail on premium; backtests."""
from datetime import datetime, timezone

import pandas as pd

from syena.algos import backtest
from syena.algos.rules import canon_rule
from syena.execution import trailing
from syena.execution.brokers import PaperBroker
from syena.models import Style, Trade, TradeStatus
from syena.monitor import check_price_levels

pf = lambda db, mode: PaperBroker()  # noqa: E731
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)


def trade(db, **kw):
    t = Trade(source=kw.pop("source", "news"), mode="PAPER", style=kw.pop("style", Style.SWING), stock=kw.pop("stock", "INFY"),
              qty=kw.pop("qty", 10), invested_amount=1000, entry_price=kw.pop("entry", 100.0), stop_loss=kw.pop("stop", 95.0),
              target=kw.pop("target", 200.0), status=TradeStatus.OPEN, auto_sell=True, **kw)
    db.add(t)
    db.commit()
    return t


def test_pure_rule_long_and_short():
    st = trailing.init_state(Trade(entry_price=100.0, stop_loss=95.0, side="BUY", source="news", style=Style.SWING), trailing.DEFAULTS)
    stop, st = trailing.new_stop(st, 100, 95, 103, short=False)
    assert stop == 95  # not yet +1R
    stop, st = trailing.new_stop(st, 100, stop, 105, short=False)
    assert stop == 100  # +1R: break-even
    stop, st = trailing.new_stop(st, 100, stop, 112, short=False)
    assert stop == 107  # best 112 - 5
    stop, st = trailing.new_stop(st, 100, stop, 104, short=False)
    assert stop == 107 and st["best"] == 112  # never moves back
    sh = trailing.init_state(Trade(entry_price=100.0, stop_loss=105.0, side="SELL", source="news", style=Style.INTRADAY), trailing.DEFAULTS)
    stop, sh = trailing.new_stop(sh, 100, 105, 90, short=True)
    assert stop == 95


def test_monitor_trails_then_exits_as_trailing_stop(db, user):
    t = trade(db)
    check_price_levels(db, {"INFY": 112.0}, NOW, broker_factory=pf)
    db.refresh(t)
    assert t.stop_loss == 107 and t.trail["moved"] >= 1 and t.trail["initial_stop"] == 95
    check_price_levels(db, {"INFY": 106.5}, NOW, broker_factory=pf)
    db.refresh(t)
    assert t.status == TradeStatus.CLOSED and t.exit_reason == "trailing_stop" and t.profit_loss > 0


def test_manual_long_term_is_not_trailed(db, user):
    t = trade(db, source="manual", style=Style.LONG_TERM)
    check_price_levels(db, {"INFY": 150.0}, NOW, broker_factory=pf)
    db.refresh(t)
    assert t.stop_loss == 95 and t.trail is None


def test_options_trail_on_premium(db, user):
    t = trade(db, source="manual", stock="NIFTY25000CE", instrument="OPT", entry=100.0, stop=70.0, target=500.0,
              option={"underlying": "NIFTY", "type": "CE", "lot_size": 75})
    check_price_levels(db, {"NIFTY25000CE": 115.0}, NOW, broker_factory=pf)
    db.refresh(t)
    assert t.stop_loss == 70  # +15%: not active yet
    check_price_levels(db, {"NIFTY25000CE": 160.0}, NOW, broker_factory=pf)
    db.refresh(t)
    assert t.stop_loss == 120  # 25% below the 160 peak


def test_backtest_trails_without_lookahead():
    idx = pd.bdate_range("2026-01-01", periods=8, tz="UTC")
    # buy signal on bar 0 -> fill bar 1 open 100; stop 2% (98); rally to 110 high, then a drop to 104
    rows = [(100, 100, 100, 100), (100, 101, 99.5, 100.5), (101, 106, 100.5, 105), (105, 110, 104.5, 109),
            (108, 108, 103.9, 104), (104, 104, 104, 104), (104, 104, 104, 104), (104, 104, 104, 104)]
    df = pd.DataFrame([{"open": o, "high": h, "low": l, "close": c, "volume": 1} for o, h, l, c in rows], index=idx)
    rule = canon_rule({"entry": {"op": "compare", "left": {"kind": "price", "field": "close"}, "cmp": ">=", "right": {"kind": "value", "value": 100}},
                       "stop_loss": {"type": "pct", "value": 2}, "target": {"type": "pct", "value": 40}})
    res = backtest.run(df, rule, backtest.BacktestConfig(max_hold_days=30, starting_capital=1e6, slippage_bps=0, min_amount=0))
    t = res["trades"][0]
    assert t["exit_reason"] == "trailing_stop" and t["initial_stop"] == 98.0 and t["stop"] == 108.0 and t["exit"] == 108.0
    off = backtest.run(df, rule, backtest.BacktestConfig(max_hold_days=30, starting_capital=1e6, slippage_bps=0, min_amount=0, trail_activate_r=None))
    assert off["trades"][0]["exit_reason"] != "trailing_stop"
