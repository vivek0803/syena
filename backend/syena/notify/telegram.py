"""Telegram bot: alerts with Approve/Reject buttons, /halt kill switch, /status.

Inbound updates arrive at POST /api/telegram/webhook and are accepted only if the secret header matches
SYENA_TELEGRAM_WEBHOOK_SECRET and the chat is SYENA_TELEGRAM_CHAT_ID. Telegram can never switch to LIVE.
"""
import html
import logging
import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import utcnow
from syena.models import Alert, Trade, TradeStatus

log = logging.getLogger("syena.telegram")


def enabled() -> bool:
    s = get_settings()
    return bool(s.telegram_bot_token.get_secret_value() and s.telegram_chat_id)


def _api(method: str, payload: dict, http=httpx) -> dict:
    token = get_settings().telegram_bot_token.get_secret_value()
    r = http.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=15)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method} failed: {data.get('description')}")
    return data.get("result") or {}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def send(text: str, buttons: list[list[dict]] | None = None, http=httpx) -> int | None:
    if not enabled():
        log.info("telegram not configured; message not sent", extra={"preview": text[:80]})
        return None
    payload = {"chat_id": get_settings().telegram_chat_id, "text": text[:4000], "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        return _api("sendMessage", payload, http).get("message_id")
    except Exception as e:
        log.error("telegram send failed", extra={"error": str(e)})
        return None


def edit(message_id: int, text: str, buttons=None) -> None:
    if not enabled() or not message_id:
        return
    payload = {"chat_id": get_settings().telegram_chat_id, "message_id": message_id, "text": text[:4000], "parse_mode": "HTML"}
    payload["reply_markup"] = {"inline_keyboard": buttons or []}
    try:
        _api("editMessageText", payload)
    except Exception as e:
        log.info("telegram edit failed", extra={"error": str(e)})


def notify(db: Session, kind: str, text: str, symbol: str | None = None, trade_id: int | None = None) -> Alert:
    a = Alert(kind=kind, symbol=symbol, trade_id=trade_id, message=text)
    db.add(a)
    db.flush()
    a.telegram_message_id = send(text)
    a.status = "SENT" if a.telegram_message_id or not enabled() else "FAILED"
    db.commit()
    return a


def sell_alert(db: Session, trade: Trade, reason: str, detail: str) -> Alert | None:
    """One open SELL alert per trade at a time."""
    if db.scalar(select(Alert).where(Alert.kind == "sell", Alert.trade_id == trade.id, Alert.status == "SENT")):
        return None
    a = Alert(kind="sell", symbol=trade.stock, trade_id=trade.id, message=f"{reason}: {detail}",
              payload={"reason": reason, "detail": detail})
    db.add(a)
    db.flush()
    pnl = ""
    if trade.last_price and trade.entry_price:
        pnl = f"\nUnrealised: ₹{(trade.last_price - trade.entry_price) * trade.qty:,.0f} (LTP {trade.last_price})"
    text = (f"🔔 <b>SELL alert</b> [{esc(trade.mode)}]\n<b>{esc(trade.stock)}</b> × {trade.qty} @ entry {trade.entry_price}{pnl}\n"
            f"Reason: <b>{esc(reason)}</b>\n{esc(detail)}")
    a.telegram_message_id = send(text, [[{"text": "✅ Approve sell", "callback_data": f"sell:ok:{a.id}"},
                                         {"text": "❌ Reject (snooze)", "callback_data": f"sell:no:{a.id}"}]])
    db.commit()
    return a


def verify_webhook(secret_header: str | None, chat_id) -> bool:
    s = get_settings()
    expected = s.telegram_webhook_secret.get_secret_value()
    return bool(expected) and bool(secret_header) and secrets.compare_digest(expected, secret_header) and str(chat_id) == str(s.telegram_chat_id)


def chat_id_of(update: dict):
    if "callback_query" in update:
        return ((update["callback_query"].get("message") or {}).get("chat") or {}).get("id")
    return ((update.get("message") or {}).get("chat") or {}).get("id")


def handle_update(db: Session, update: dict, approve_sell, reject_sell, halt, status_text) -> str:
    """Dispatch a verified update. Callables are injected so this module stays free of trading logic."""
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        try:
            _api("answerCallbackQuery", {"callback_query_id": cq["id"]})
        except Exception:
            pass
        parts = data.split(":")
        if len(parts) != 3 or parts[0] not in ("sell", "live"):
            return "ignored"
        alert = db.get(Alert, int(parts[2])) if parts[2].isdigit() else None
        if alert is None or alert.status not in ("SENT", "CONFIRMING"):
            return "stale"
        trade = db.get(Trade, alert.trade_id)
        if trade is None or trade.status != TradeStatus.OPEN:
            alert.status, alert.resolved_at = "EXPIRED", utcnow()
            db.commit()
            edit(alert.telegram_message_id, f"{esc(alert.message)}\n\n<i>Position no longer open.</i>")
            return "expired"
        if parts[0] == "sell" and parts[1] == "ok" and trade.mode == "LIVE":
            alert.status = "CONFIRMING"  # LIVE sells need a second tap
            db.commit()
            edit(alert.telegram_message_id, f"⚠️ Confirm <b>LIVE</b> sell of {esc(trade.stock)} × {trade.qty}?",
                 [[{"text": "🔴 Yes, sell LIVE", "callback_data": f"live:ok:{alert.id}"},
                   {"text": "Cancel", "callback_data": f"sell:no:{alert.id}"}]])
            return "confirming"
        if parts[1] == "ok":
            result = approve_sell(db, alert, trade)
            alert.status, alert.resolved_at = "APPROVED", utcnow()
            db.commit()
            edit(alert.telegram_message_id, f"✅ Approved: {esc(result)}")
            return "approved"
        reject_sell(db, alert, trade)
        alert.status, alert.resolved_at = "REJECTED", utcnow()
        db.commit()
        edit(alert.telegram_message_id, f"❌ Rejected: {esc(trade.stock)} kept; alerts snoozed.")
        return "rejected"
    text = ((update.get("message") or {}).get("text") or "").strip()
    if text.startswith("/halt"):
        reason = text[5:].strip() or "Telegram /halt"
        halt(db, reason)
        send(f"🛑 <b>Trading HALTED</b>: {esc(reason)}\nMode is PAPER. Resume from the web UI.")
        return "halted"
    if text.startswith("/status"):
        send(status_text(db))
        return "status"
    if text.startswith("/live") or text.startswith("/mode"):
        send("Mode can only be changed in the web UI (password + 2FA). Telegram can only /halt.")
        return "refused"
    send("Commands: /status, /halt [reason]")
    return "help"


def set_webhook(url: str) -> dict:
    s = get_settings()
    return _api("setWebhook", {"url": url, "secret_token": s.telegram_webhook_secret.get_secret_value(),
                               "allowed_updates": ["message", "callback_query"], "drop_pending_updates": True})
