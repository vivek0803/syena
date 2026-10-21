"""Route a verified Telegram update to Syena's actions. Shared by the webhook (server) and polling (laptop)."""
import logging

from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import utcnow
from syena.market import kite as kite_session
from syena.market import source as md_source
from syena.models import AppSetting
from syena.monitor import approve_sell, reject_sell, status_text
from syena.notify import telegram
from syena.services import mode as mode_service

log = logging.getLogger("syena.telegram")
OFFSET_KEY = "_telegram_offset"


def dispatch(db: Session, update: dict) -> str:
    md = md_source.get_market_data(db) if md_source.ready(db) else None
    return telegram.handle_update(
        db, update,
        approve_sell=lambda d, a, t: approve_sell(d, a, t, md),
        reject_sell=reject_sell,
        halt=lambda d, reason: mode_service.halt(d, reason, actor="telegram"),
        status_text=status_text,
    )


def poll_once(db: Session, api=None) -> int:
    """Polling mode (no public URL needed): fetch new updates, accept only those from your chat ID."""
    api = api or telegram._api
    row = db.get(AppSetting, OFFSET_KEY)
    offset = int(row.value) if row and row.value is not None else 0
    updates = api("getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["message", "callback_query"]})
    handled = 0
    for u in updates or []:
        offset = max(offset, int(u["update_id"]) + 1)
        if str(telegram.chat_id_of(u)) != str(get_settings().telegram_chat_id):
            log.warning("ignored Telegram update from another chat")
            continue
        try:
            dispatch(db, u)
            handled += 1
        except Exception as e:
            log.error("telegram update failed", extra={"error": str(e)})
    if row is None:
        db.add(AppSetting(key=OFFSET_KEY, value=offset, updated_at=utcnow()))
    else:
        row.value, row.updated_at = offset, utcnow()
    db.commit()
    return handled


def start_polling(api=None) -> None:
    """Telegram refuses getUpdates while a webhook is set, so remove any webhook first."""
    (api or telegram._api)("deleteWebhook", {"drop_pending_updates": False})
