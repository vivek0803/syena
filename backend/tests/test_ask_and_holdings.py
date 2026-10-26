"""Ask AI (market questions only, grounded in Syena data) and news reaching the stocks you own."""
from datetime import timedelta

from sqlalchemy import select

from syena import ask
from syena.models import Alert, Decision, DecisionType, NewsItem, Stock, Style, Trade, TradeStatus
from syena.news import pipeline
from syena.portfolio.mine import my_stocks
from tests.test_news import _news_now, pf


def _manual_trade(db, sym="INFY"):
    t = Trade(source="manual", mode="PAPER", style=Style.LONG_TERM, stock=sym, side="BUY", qty=10, invested_amount=15000,
              entry_price=1500, status=TradeStatus.OPEN)
    db.add(t)
    db.commit()
    return t


def test_my_stocks_includes_manual_trades(db):
    _manual_trade(db)
    mine = my_stocks(db)
    assert mine["INFY"]["qty"] == 10 and mine["INFY"]["sources"] == ["manual long-term (paper)"]


def test_ask_uses_stock_data_and_portfolio(db, user, fake_llm, md):
    db.add(Stock(symbol="INFY", name="Infosys", active=True))
    db.commit()
    _manual_trade(db)
    out = ask.answer(db, "Should I add more INFY to my portfolio?", [], md)
    assert out["in_scope"] and out["symbols"] == ["INFY"] and out["used_portfolio"]
    prompt = fake_llm.prompts[-1]
    assert "### INFY" in prompt and "You own it" in prompt and "Market now: NIFTY 25000" in prompt
    assert "<untrusted" in prompt


def test_ask_follow_up_keeps_the_stock(db, user, fake_llm, md):
    db.add(Stock(symbol="INFY", name="Infosys", active=True))
    db.commit()
    out = ask.answer(db, "and what about its debt?", [{"role": "user", "content": "Tell me about INFY"},
                                                      {"role": "assistant", "content": "..."}], md)
    assert out["symbols"] == ["INFY"]


def test_off_topic_questions_are_declined(db, user, fake_llm, md):
    fake_llm.ask = {"in_scope": False, "answer": "Paris is the capital of France"}
    out = ask.answer(db, "What is the capital of France?", [], md)
    assert not out["in_scope"] and out["answer"] == ask.REFUSAL and "Paris" not in out["answer"]


def test_ask_api(authed, fake_llm):
    r = authed.post("/api/ask", json={"question": "What is a P/E ratio?", "history": []})
    assert r.status_code == 200 and r.json()["in_scope"]
    assert authed.post("/api/ask", json={"question": "", "history": []}).status_code == 422


def test_news_on_a_holding_alerts_you(db, user, fake_llm, md, monkeypatch):
    from syena.notify import telegram

    sent = []
    monkeypatch.setattr(telegram, "send", lambda text, buttons=None, http=None: sent.append(text) or 1)
    now = _news_now(md)
    _manual_trade(db)
    item = NewsItem(source="t", url="u", title="Infosys wins $1.5 billion deal", published_at=now - timedelta(minutes=5), content_hash="h-own")
    db.add(item)
    db.commit()
    pipeline.process_symbol(db, "INFY", [item], md, "order_win", pf, now=now)
    a = db.scalar(select(Alert).where(Alert.kind == "holding_news"))
    assert a and "News on your holding INFY" in a.message and "10 shares" in a.message and "/stock/INFY" in a.message


def test_no_short_on_a_stock_you_hold_in_your_demat(db, user, fake_llm, md, monkeypatch):
    from syena.portfolio import mine as mine_mod

    monkeypatch.setattr(mine_mod, "my_stocks", lambda db_: {"INFY": {"qty": 5, "avg_price": 1400, "value": 7500,
                                                                     "sources": ["zerodha holding"], "trade_ids": []}})
    fake_llm.analysis = {**fake_llm.analysis, "action": "SELL", "confidence": 90}
    now = _news_now(md)
    item = NewsItem(source="t", url="u", title="Infosys CFO resigns", published_at=now - timedelta(minutes=5), content_hash="h-cfo")
    db.add(item)
    db.commit()
    sig = pipeline.process_symbol(db, "INFY", [item], md, "management", pf, now=now)
    d = db.scalar(select(Decision).where(Decision.signal_id == sig.id))
    assert d.type == DecisionType.REJECTED and "no short" in d.summary
    assert db.scalar(select(Trade).where(Trade.side == "SELL")) is None


def test_portfolio_news_endpoint(authed, db, fake_llm, md):
    _manual_trade(db)
    r = authed.get("/api/portfolio/news").json()
    assert r["holdings"] == 1 and r["quiet"] == ["INFY"]
