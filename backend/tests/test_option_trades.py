"""Paper option trades: lot sizing, F&O charges, contract choice, PAPER-only routing, exits, news and algo hooks."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from syena.execution import engine
from syena.execution.brokers import KiteBroker, LiveOrderForbidden, OrderRequest, PaperBroker
from syena.execution.sizing import size_option
from syena.market import options as op
from syena.market import options_trade as ot
from syena.market.charges import option_leg_charges
from syena.models import Decision, DecisionType, Trade, TradeStatus, TradingMode
from syena.monitor import check_price_levels
from syena.services import mode as mode_service
from tests.test_options import FakeAngel, seed

pf = lambda db, mode: PaperBroker()  # noqa: E731


def test_lot_sizing():
    ok = size_option(100.0, 75, 2000, 15000, 3)
    assert ok.ok and ok.qty == 75 * 2 and ok.amount == 15000  # 2 lots fit the ₹15,000 budget
    assert size_option(100.0, 75, 2000, 15000, 1).qty == 75  # capped at max_lots
    assert not size_option(300.0, 75, 2000, 15000, 3).ok  # one lot is ₹22,500
    assert not size_option(1.0, 75, 2000, 15000, 3).ok  # three lots = ₹225, below the minimum
    assert size_option(100.0, 75, 2000, 15000, 3, lots=1).qty == 75


def test_option_charges():
    buy, sell = option_leg_charges("BUY", 75, 100.0), option_leg_charges("SELL", 75, 120.0)
    assert buy.brokerage == 20 and buy.stt == 0 and buy.stamp > 0
    assert sell.stt == pytest.approx(75 * 120 * 0.0015) and sell.stamp == 0


def test_contract_choice_skips_expiries_too_close(db):
    near, nxt = date.today() + timedelta(days=2), date.today() + timedelta(days=9)
    seed(db, near)
    seed(db, nxt)
    now = datetime.now(timezone.utc)
    c = ot.pick_contract(db, "NIFTY", "BUY", 25040, now)
    assert c.expiry == nxt and c.option_type == "CE" and c.strike == 25050  # ATM call, next expiry (4+ days)
    p = ot.pick_contract(db, "NIFTY", "SELL", 25040, now, strike_pref="ITM")
    assert p.option_type == "PE" and p.strike == 25100


def test_enter_option_is_paper_even_when_system_is_live(db, user):
    seed(db, date.today() + timedelta(days=9))
    st = mode_service.get_state(db)
    st.mode = TradingMode.LIVE  # the whole system LIVE: options still only simulate
    db.commit()
    now = datetime.now(timezone.utc).replace(hour=5)  # 10:30 IST
    d = ot.enter_option(db, FakeAngel(), "NIFTY", "BUY", source="manual", now=now, broker_factory=pf, lots=1)
    assert d.type == DecisionType.PLACED, d.summary
    t = db.get(Trade, d.trade_id)
    assert t.mode == "PAPER" and t.instrument == "OPT" and t.stock == "NIFTY25050CE" and t.qty == 75
    assert t.option["underlying"] == "NIFTY" and t.option["lots"] == 1 and t.option["iv_at_entry"]
    assert t.stop_loss == pytest.approx(t.entry_price * 0.7, abs=0.6) and t.target > t.entry_price
    assert t.max_hold_until is not None and t.charges > 0
    assert any(c["name"] == "max_open_options" for c in d.checks_json)
    with pytest.raises(LiveOrderForbidden):  # even the live broker refuses an option order
        KiteBroker(lambda: None, lambda db: TradingMode.LIVE).place(db, OrderRequest("k", "NIFTY25050CE", "BUY", 75, "NRML", "ENTRY", 90.0, segment="OPT"))
    # one position per underlying
    d2 = ot.enter_option(db, FakeAngel(), "NIFTY", "SELL", source="manual", now=now, broker_factory=pf)
    assert d2.type == DecisionType.REJECTED and "one_position_per_underlying" in d2.summary


def test_option_stop_loss_exits_on_premium(db, user):
    seed(db, date.today() + timedelta(days=9))
    now = datetime.now(timezone.utc).replace(hour=5)
    d = ot.enter_option(db, FakeAngel(), "NIFTY", "BUY", source="manual", now=now, broker_factory=pf, lots=1)
    t = db.get(Trade, d.trade_id)
    check_price_levels(db, {t.stock: t.stop_loss - 1}, now, broker_factory=pf)
    db.refresh(t)
    assert t.status == TradeStatus.CLOSED and t.exit_reason == "stop_loss" and t.profit_loss < 0


def test_news_verdict_also_trades_an_option(db, user, fake_llm, md, monkeypatch):
    from tests.test_news import _news_now
    from syena.models import NewsItem
    from syena.news import pipeline

    calls = []
    monkeypatch.setattr(ot, "has_options", lambda db, s: True)
    monkeypatch.setattr(ot, "enter_option", lambda db, md, sym, direction, **kw: calls.append((sym, direction, kw["algo_id"], kw["idempotency_key"])))
    now = _news_now(md)
    item = NewsItem(source="t", url="u", title="Infosys wins $1.5 billion deal", published_at=now - timedelta(minutes=10), content_hash="o1")
    db.add(item)
    db.commit()
    sig = pipeline.process_symbol(db, "INFY", [item], md, "order_win", pf, now=now)
    d = db.scalar(select(Decision).where(Decision.signal_id == sig.id))
    assert d.type == DecisionType.PLACED, d.summary
    if True:
        assert calls and calls[0][0] == "INFY" and calls[0][1] == "BUY" and calls[0][3] == f"news-sig{sig.id}-opt"
        assert ot.news_options_algo(db).id == calls[0][2]


def test_low_confidence_news_does_not_trade_options(db, user, fake_llm, md, monkeypatch):
    from tests.test_news import _news_now
    from syena.models import NewsItem
    from syena.news import pipeline

    calls = []
    monkeypatch.setattr(ot, "has_options", lambda db, s: True)
    monkeypatch.setattr(ot, "enter_option", lambda *a, **k: calls.append(a))
    fake_llm.analysis = {**fake_llm.analysis, "confidence": 40}
    now = _news_now(md)
    item = NewsItem(source="t", url="u", title="Infosys deal", published_at=now, content_hash="o2")
    db.add(item)
    db.commit()
    pipeline.process_symbol(db, "INFY", [item], md, None, pf, now=now)
    assert not calls


def test_trades_api(authed, db):
    db.add(Trade(source="manual", mode="PAPER", style=engine.Style.SWING, stock="NIFTY25050CE", instrument="OPT",
                 option={"underlying": "NIFTY", "type": "CE", "strike": 25050, "expiry": "2026-11-04", "lots": 1, "lot_size": 75},
                 qty=75, invested_amount=6750, entry_price=90, exit_price_executed=120, profit_loss=2190, status=TradeStatus.CLOSED,
                 closed_at=datetime.now(timezone.utc)))
    db.commit()
    r = authed.get("/api/options/trades").json()
    assert r["summary"]["trades"] == 1 and r["summary"]["win_rate"] == 100.0 and r["trades"][0]["underlying"] == "NIFTY"
