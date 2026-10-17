"""Position monitor, Telegram approvals, /halt, webhook verification."""
from datetime import datetime

import pytest
from sqlalchemy import select

from syena.config import get_settings
from syena.execution.brokers import PaperBroker
from syena.market.calendar import IST
from syena.models import Alert, Style, Trade, TradeStatus, TradingMode
from syena.monitor import approve_sell, check_price_levels, reject_sell, status_text
from syena.notify import telegram
from syena.services import mode as mode_service

NOW = datetime(2026, 10, 16, 11, 0, tzinfo=IST)
pf = lambda db, mode: PaperBroker()  # noqa: E731
SECRET = "test-webhook-secret"


@pytest.fixture
def tg(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "telegram_chat_id", "42")
    from pydantic import SecretStr

    monkeypatch.setattr(s, "telegram_webhook_secret", SecretStr(SECRET))
    monkeypatch.setattr(s, "telegram_bot_token", SecretStr("123:abc"))
    sent = []

    def fake_api(method, payload, http=None):
        sent.append((method, payload))
        return {"message_id": len(sent)}

    monkeypatch.setattr(telegram, "_api", fake_api)
    return sent


def open_trade(db, auto_sell=False, mode="PAPER", style=Style.SWING):
    t = Trade(source="algo", mode=mode, style=style, stock="INFY", qty=6, invested_amount=9000, entry_price=1500,
              stop_loss=1470, target=1560, status=TradeStatus.OPEN, auto_sell=auto_sell, charges=10)
    db.add(t)
    db.commit()
    return t


def test_stop_loss_auto_sells(db, user, tg):
    t = open_trade(db, auto_sell=True)
    check_price_levels(db, {"INFY": 1460}, NOW, pf)
    assert t.status == TradeStatus.CLOSED and t.exit_reason == "stop_loss" and t.profit_loss < 0


def test_condition_without_auto_sell_sends_one_alert(db, user, tg):
    t = open_trade(db, auto_sell=False)
    check_price_levels(db, {"INFY": 1565}, NOW, pf)
    check_price_levels(db, {"INFY": 1566}, NOW, pf)
    alerts = db.scalars(select(Alert).where(Alert.kind == "sell")).all()
    assert len(alerts) == 1 and t.status == TradeStatus.OPEN
    kb = [p for m, p in tg if m == "sendMessage"][-1]["reply_markup"]["inline_keyboard"][0]
    assert kb[0]["callback_data"] == f"sell:ok:{alerts[0].id}"


def test_intraday_square_off_is_always_automatic(db, user, tg):
    t = open_trade(db, auto_sell=False, style=Style.INTRADAY)
    check_price_levels(db, {"INFY": 1500}, datetime(2026, 10, 16, 15, 11, tzinfo=IST), pf)
    assert t.status == TradeStatus.CLOSED and t.exit_reason == "square_off"


def _update(data):
    return {"callback_query": {"id": "cb1", "data": data, "message": {"chat": {"id": 42}}}}


def test_webhook_rejects_bad_secret_or_chat(client, tg):
    assert client.post("/api/telegram/webhook", json={"message": {"chat": {"id": 42}, "text": "/halt"}},
                       headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 403
    assert client.post("/api/telegram/webhook", json={"message": {"chat": {"id": 7}, "text": "/halt"}},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}).status_code == 403


def test_halt_via_telegram_and_no_live_switch(client, db, tg):
    h = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    r = client.post("/api/telegram/webhook", json={"message": {"chat": {"id": 42}, "text": "/live"}}, headers=h)
    assert r.json()["result"] == "refused"
    r = client.post("/api/telegram/webhook", json={"message": {"chat": {"id": 42}, "text": "/halt market looks crazy"}}, headers=h)
    assert r.json()["result"] == "halted"
    db.expire_all()
    st = mode_service.get_state(db)
    assert st.halted and st.mode == TradingMode.PAPER and "crazy" in st.halt_reason


def test_approve_and_reject_flow(db, user, tg):
    t = open_trade(db)
    a = telegram.sell_alert(db, t, "target", "LTP ≥ target")
    t.last_price = 1570
    db.commit()
    res = telegram.handle_update(db, _update(f"sell:no:{a.id}"), approve_sell, reject_sell, lambda *_: None, status_text)
    assert res == "rejected" and t.snoozed_until is not None and t.status == TradeStatus.OPEN
    a2 = telegram.sell_alert(db, t, "target", "again")
    res = telegram.handle_update(db, _update(f"sell:ok:{a2.id}"),
                                 lambda d, al, tr: __import__("syena.execution.engine", fromlist=["x"]).exit_trade(
                                     d, tr, "target", 1570, actor="user", broker_factory=pf).summary,
                                 reject_sell, lambda *_: None, status_text)
    assert res == "approved" and t.status == TradeStatus.CLOSED
    # stale button after close
    assert telegram.handle_update(db, _update(f"sell:ok:{a2.id}"), approve_sell, reject_sell, lambda *_: None, status_text) == "stale"


def test_live_sell_needs_second_tap(db, user, tg):
    t = open_trade(db, mode="LIVE")
    a = telegram.sell_alert(db, t, "negative_news", "guidance cut")
    called = []
    res = telegram.handle_update(db, _update(f"sell:ok:{a.id}"), lambda *x: called.append(x) or "sold", reject_sell, lambda *_: None, status_text)
    assert res == "confirming" and not called
    res = telegram.handle_update(db, _update(f"live:ok:{a.id}"), lambda *x: called.append(x) or "sold", reject_sell, lambda *_: None, status_text)
    assert res == "approved" and called
