"""After-hours signals wait in Syena's own queue (any style, buy or short) and Syena places them itself as normal
orders at the next open; gaps beyond the limit wait, then cancel at the cutoff; live orders are reconciled."""
from datetime import datetime

from sqlalchemy import select

from syena.execution import engine
from syena.execution.brokers import KiteBroker, PaperBroker, get_wallet
from syena.execution.pending import process_pending
from syena.market.calendar import IST
from syena.models import Decision, DecisionType, Order, OrderStatus, Style, Trade, TradeStatus, TradingMode
from syena.services import mode as mode_service

SAT_NIGHT = datetime(2026, 10, 17, 21, 45, tzinfo=IST)
MON_PREOPEN = datetime(2026, 10, 19, 9, 15, 30, tzinfo=IST)
MON_OPEN = datetime(2026, 10, 19, 9, 16, 40, tzinfo=IST)
MON_LATE = datetime(2026, 10, 19, 10, 5, tzinfo=IST)
PF = lambda db, mode: PaperBroker()  # noqa: E731


class Quotes:
    def __init__(self, p):
        self.p = p

    def ltp(self, syms):
        return {s: self.p for s in syms}


def queue(db, price=1500.0, style=Style.SWING, side="BUY", stop=None, target=None):
    it = engine.TradeIntent(symbol="INFY", style=style, source="algo", ref_price=price, side=side, stop_loss=stop, target=target)
    d = engine.enter(db, it, PF, now=SAT_NIGHT)
    return d, db.get(Trade, d.trade_id)


def test_after_hours_signal_is_queued_in_syena_not_sent_to_broker(db, user):
    d, t = queue(db)
    o = db.scalar(select(Order).where(Order.trade_id == t.id))
    assert d.type == DecisionType.PLACED and d.summary.startswith("QUEUED BUY") and t.status == TradeStatus.PENDING
    assert o.variety == "queued" and o.broker_order_id is None and o.price == 1500.0
    assert get_wallet(db).cash == 100000  # nothing bought yet
    assert process_pending(db, Quotes(1510), now=SAT_NIGHT, broker_factory=PF)["still_open"] == 1
    assert process_pending(db, Quotes(1510), now=MON_PREOPEN, broker_factory=PF)["still_open"] == 1  # waits for 09:16


def test_queued_order_placed_by_syena_at_open_and_stops_follow_fill(db, user):
    d, t = queue(db, stop=1470.0, target=1560.0)
    out = process_pending(db, Quotes(1510), now=MON_OPEN, broker_factory=PF)
    db.refresh(t)
    assert out["filled"] == 1 and t.status == TradeStatus.OPEN and 1510 <= t.entry_price <= 1512
    shift = t.entry_price - 1500.0
    assert t.stop_loss == round(1470 + shift, 2) and t.target == round(1560 + shift, 2)  # same planned risk
    assert get_wallet(db).cash < 100000


def test_intraday_buys_and_shorts_are_queued_too(db, user):
    _, long_ = queue(db, style=Style.INTRADAY)
    d, short = queue(db, price=1500.0, style=Style.INTRADAY, side="SELL")
    assert d.summary.startswith("QUEUED SHORT SELL")
    out = process_pending(db, Quotes(1495), now=MON_OPEN, broker_factory=PF)
    db.refresh(long_), db.refresh(short)
    assert out["filled"] == 2 and long_.status == short.status == TradeStatus.OPEN
    o = db.scalar(select(Order).where(Order.trade_id == short.id))
    assert o.product == "MIS" and o.side == "SELL"


def test_gap_beyond_limit_waits_then_cancels_with_reason(db, user):
    _, t = queue(db)
    assert process_pending(db, Quotes(1600), now=MON_OPEN, broker_factory=PF)["still_open"] == 1  # +6.7% gap
    out = process_pending(db, Quotes(1600), now=MON_LATE, broker_factory=PF)
    db.refresh(t)
    assert out["cancelled"] == 1 and t.status == TradeStatus.FAILED and "+6.7%" in t.exit_reason
    assert get_wallet(db).cash == 100000
    _, s = queue(db, side="SELL", style=Style.INTRADAY)  # a short is hurt by a gap DOWN
    assert process_pending(db, Quotes(1450), now=MON_OPEN, broker_factory=PF)["still_open"] == 1


def test_halt_cancels_queued(db, user):
    _, t = queue(db)
    mode_service.halt(db, "test", actor="user")
    process_pending(db, Quotes(1500), now=SAT_NIGHT, broker_factory=PF)
    db.refresh(t)
    o = db.scalar(select(Order).where(Order.trade_id == t.id))
    assert t.status == TradeStatus.FAILED and o.status == OrderStatus.CANCELLED


def test_queue_off_rejects_with_reason(db, user):
    from syena import settings_store

    settings_store.put(db, "amo", {"enabled": False})
    d, _ = queue(db)
    assert d.type == DecisionType.REJECTED and "queueing for the next open is turned off" in d.summary


class FakeKite:
    def __init__(self):
        self.placed, self.cancelled, self.status = [], [], "OPEN"

    def orders(self):
        return []

    def place_order(self, **p):
        self.placed.append(p)
        return "OID1"

    def order_history(self, oid):
        h = {"status": self.status, "variety": "regular"}
        if self.status == "COMPLETE":
            h.update(average_price=1505.5, filled_quantity=6)
        return [h]

    def cancel_order(self, variety, order_id):
        self.cancelled.append((variety, order_id))


def _live(db, monkeypatch, kite):
    monkeypatch.setattr(mode_service, "current_mode", lambda db: TradingMode.LIVE)
    return lambda db, mode: KiteBroker(lambda: kite, lambda d: TradingMode.LIVE, sleep=lambda s: None)  # noqa: E731


def test_live_queued_order_sent_by_syena_at_open_as_regular_and_reconciled(db, user, monkeypatch):
    kite = FakeKite()
    factory = _live(db, monkeypatch, kite)
    it = engine.TradeIntent(symbol="INFY", style=Style.SWING, source="manual", ref_price=1500.0)
    t = db.get(Trade, engine.enter(db, it, factory, now=SAT_NIGHT).trade_id)
    assert kite.placed == [] and t.status == TradeStatus.PENDING and t.mode == "LIVE"  # nothing at the broker overnight
    process_pending(db, Quotes(1504), now=MON_OPEN, kite_fn=lambda: kite, broker_factory=factory)
    assert kite.placed[0]["variety"] == "regular" and kite.placed[0]["order_type"] == "LIMIT"
    kite.status = "COMPLETE"
    process_pending(db, Quotes(1504), now=MON_OPEN, kite_fn=lambda: kite, broker_factory=factory)
    db.refresh(t)
    assert t.status == TradeStatus.OPEN and t.entry_price == 1505.5


def test_live_unfilled_after_cutoff_cancelled(db, user, monkeypatch):
    kite = FakeKite()
    factory = _live(db, monkeypatch, kite)
    it = engine.TradeIntent(symbol="INFY", style=Style.SWING, source="manual", ref_price=1500.0)
    t = db.get(Trade, engine.enter(db, it, factory, now=SAT_NIGHT).trade_id)
    process_pending(db, Quotes(1504), now=MON_OPEN, kite_fn=lambda: kite, broker_factory=factory)
    process_pending(db, Quotes(1504), now=MON_LATE, kite_fn=lambda: kite, broker_factory=factory)
    db.refresh(t)
    assert kite.cancelled and t.status == TradeStatus.FAILED
