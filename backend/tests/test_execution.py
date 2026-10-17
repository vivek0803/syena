"""Deterministic tests (17E): sizing, risk limits, trade gate, intraday rules, PaperBroker, broker routing."""
from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from syena import settings_store
from syena.execution import engine
from syena.execution.brokers import KiteBroker, LiveOrderForbidden, OrderRequest, PaperBroker, get_wallet
from syena.execution.checks import Check
from syena.execution.sizing import size_position
from syena.market.calendar import IST
from syena.market.charges import leg_charges, round_trip
from syena.models import Algo, DecisionType, Order, Style, Trade, TradeStatus, TradingMode
from syena.services import mode as mode_service

T_OPEN = datetime(2026, 10, 16, 10, 0, tzinfo=IST)  # Friday, trading day
T_LATE = datetime(2026, 10, 16, 14, 50, tzinfo=IST)
T_HOLIDAY = datetime(2026, 10, 20, 10, 0, tzinfo=IST)  # Dussehra
T_NIGHT = datetime(2026, 10, 16, 20, 0, tzinfo=IST)


def intent(symbol="INFY", price=1500.0, style=Style.SWING, **kw):
    return engine.TradeIntent(symbol=symbol, style=style, source=kw.pop("source", "algo"), ref_price=price, **kw)


def paper_factory(db, mode):
    assert mode == "PAPER", "tests must never route to a live broker"
    return PaperBroker()


# ---------------------------------------------------------------- sizing
@pytest.mark.parametrize("price,qty", [(1500, 6), (100, 100), (7000, 1), (9999.5, 1), (3333.33, 3)])
def test_sizing_valid(price, qty):
    r = size_position(price, 7000, 10000)
    assert r.ok and r.qty == qty and 7000 <= r.amount <= 10000


@pytest.mark.parametrize("price", [10000.01, 25000, 5000.5, 6999, 0, -5])
def test_sizing_rejects(price):
    r = size_position(price, 7000, 10000)
    assert not r.ok and r.qty == 0 and r.reason


@given(st.floats(min_value=0.05, max_value=50000, allow_nan=False))
def test_sizing_property(price):
    r = size_position(price, 7000, 10000)
    if r.ok:
        assert r.qty >= 1 and 7000 <= r.qty * price <= 10000
    else:
        assert price > 10000 or int(10000 // price) * price < 7000


# ---------------------------------------------------------------- charges
def test_charges_intraday_brokerage_capped():
    big = leg_charges("BUY", 100, 5000, intraday=True)
    assert big.brokerage == 20.0 and big.stt == 0 and big.stamp > 0
    small = leg_charges("SELL", 5, 1500, intraday=True)
    assert small.brokerage == pytest.approx(2.25) and small.stt == pytest.approx(1.875)


def test_charges_delivery_has_stt_both_sides_and_dp_on_sell():
    buy, sell = leg_charges("BUY", 6, 1500, False), leg_charges("SELL", 6, 1500, False)
    assert buy.brokerage == 0 and buy.stt == pytest.approx(9.0) and buy.dp == 0
    assert sell.dp == pytest.approx(15.93)
    assert round_trip(6, 1500, 1500, False) > 30


# ---------------------------------------------------------------- engine / gate
def test_entry_placed_in_paper_with_fill_charges_and_wallet(db, user):
    d = engine.enter(db, intent(), paper_factory, now=T_OPEN)
    assert d.type == DecisionType.PLACED and d.mode == "PAPER"
    t = db.get(Trade, d.trade_id)
    assert t.status == TradeStatus.OPEN and t.qty == 6 and t.entry_price > 1500  # slippage
    assert t.charges > 0
    w = get_wallet(db)
    assert w.cash == pytest.approx(100000 - t.entry_price * 6 - t.charges, abs=0.01)
    assert {c["name"] for c in d.checks_json} >= {"sizing", "market_hours", "max_open_positions", "broker"}


@pytest.mark.parametrize("now,check", [(T_HOLIDAY, "market_hours"), (T_NIGHT, "market_hours")])
def test_closed_market_rejected(db, user, now, check):
    d = engine.enter(db, intent(), paper_factory, now=now)
    assert d.type == DecisionType.REJECTED and check in d.summary


def test_intraday_cutoff(db, user):
    d = engine.enter(db, intent(style=Style.INTRADAY), paper_factory, now=T_LATE)
    assert d.type == DecisionType.REJECTED and "intraday_cutoff" in d.summary
    assert engine.enter(db, intent(style=Style.SWING), paper_factory, now=T_LATE).type == DecisionType.PLACED


def test_sizing_failure_logged_with_reason(db, user):
    d = engine.enter(db, intent(price=12000), paper_factory, now=T_OPEN)
    assert d.type == DecisionType.REJECTED and "sizing" in d.summary and "₹10,000" in d.summary


def test_caller_check_failure_blocks(db, user):
    d = engine.enter(db, intent(extra_checks=[Check("confidence", False, "62 < 70")]), paper_factory, now=T_OPEN)
    assert d.type == DecisionType.REJECTED and d.summary.startswith("REJECTED by confidence")


def test_risk_max_open_positions(db, user):
    for i, sym in enumerate(["A", "B", "C", "D", "E"]):
        assert engine.enter(db, intent(symbol=sym), paper_factory, now=T_OPEN).type == DecisionType.PLACED
    d = engine.enter(db, intent(symbol="F"), paper_factory, now=T_OPEN)
    assert d.type == DecisionType.REJECTED and "max_open_positions" in d.summary


def test_risk_per_stock_exposure(db, user):
    engine.enter(db, intent(symbol="INFY"), paper_factory, now=T_OPEN)
    d = engine.enter(db, intent(symbol="INFY"), paper_factory, now=T_OPEN)
    assert d.type == DecisionType.REJECTED and "per_stock_exposure" in d.summary


def test_risk_max_trades_per_day(db, user):
    settings_store.put(db, "risk", {"max_trades_per_day": 2})
    for sym in ("A", "B"):
        engine.enter(db, intent(symbol=sym), paper_factory, now=T_OPEN)
    d = engine.enter(db, intent(symbol="C"), paper_factory, now=T_OPEN)
    assert "max_trades_per_day" in d.summary


def test_risk_max_daily_loss_blocks_new_entries(db, user):
    d = engine.enter(db, intent(symbol="A"), paper_factory, now=T_OPEN)
    t = db.get(Trade, d.trade_id)
    engine.exit_trade(db, t, "stop_loss", 900.0, broker_factory=paper_factory, now=T_OPEN)
    assert t.profit_loss < -3000
    d2 = engine.enter(db, intent(symbol="B"), paper_factory, now=T_OPEN)
    assert d2.type == DecisionType.REJECTED and "max_daily_loss" in d2.summary


def test_halt_blocks_entries(db, user):
    mode_service.halt(db, "test", actor="user")
    assert "not_halted" in engine.enter(db, intent(), paper_factory, now=T_OPEN).summary


def test_idempotent_entry(db, user):
    d1 = engine.enter(db, intent(idempotency_key="algo1-INFY-2026-10-16T10:00"), paper_factory, now=T_OPEN)
    d2 = engine.enter(db, intent(idempotency_key="algo1-INFY-2026-10-16T10:00"), paper_factory, now=T_OPEN)
    assert d1.id == d2.id
    assert len(db.scalars(select(Order)).all()) == 1


def test_exit_computes_pnl_and_credits_wallet(db, user):
    d = engine.enter(db, intent(), paper_factory, now=T_OPEN)
    t = db.get(Trade, d.trade_id)
    cash_before = get_wallet(db).cash
    x = engine.exit_trade(db, t, "target", 1650.0, broker_factory=paper_factory, now=T_OPEN)
    assert x.type == DecisionType.EXITED and t.status == TradeStatus.CLOSED
    expected = (t.exit_price_executed - t.entry_price) * t.qty - t.charges
    assert t.profit_loss == pytest.approx(expected, abs=0.01) and t.profit_loss > 0
    assert get_wallet(db).cash > cash_before
    # exiting twice is a no-op
    assert engine.exit_trade(db, t, "again", 1650.0, broker_factory=paper_factory).type == DecisionType.SKIPPED


def test_paper_broker_rejects_insufficient_cash(db, user):
    get_wallet(db).cash = 100
    r = PaperBroker().place(db, OrderRequest("k1", "INFY", "BUY", 6, "CNC", "ENTRY", 1500.0))
    assert r.status == "REJECTED" and "insufficient" in r.message


# ---------------------------------------------------------------- routing safety
def test_live_system_but_algo_not_gated_stays_paper(db, user):
    state = mode_service.get_state(db)
    state.mode = TradingMode.LIVE  # direct DB write simulates an already-LIVE system in this unit test
    db.commit()
    algo = Algo(name="x", english_text="x", style=Style.SWING, live_enabled=False)
    db.add(algo)
    db.commit()
    d = engine.enter(db, intent(algo_id=algo.id), paper_factory, now=T_OPEN)
    assert d.mode == "PAPER" and "not passed the LIVE gate" in d.checks_json[0]["detail"]


class _ExplodingKite:
    def __getattr__(self, name):
        raise AssertionError(f"Kite API called: {name}")


@settings(max_examples=25, deadline=None)
@given(side=st.sampled_from(["BUY", "SELL"]), qty=st.integers(1, 1000), price=st.floats(1, 9000))
def test_property_kite_broker_never_places_entry_in_paper(side, qty, price):
    kb = KiteBroker(lambda: _ExplodingKite(), lambda db: TradingMode.PAPER)
    with pytest.raises(LiveOrderForbidden):
        kb.place(None, OrderRequest("k", "INFY", side, qty, "CNC", "ENTRY", price))


def test_default_factory_routes_paper_to_paper_broker(db, user):
    assert isinstance(engine.default_broker_factory()(db, "PAPER"), PaperBroker)
