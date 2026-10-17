"""Settings, AI usage, paper wallet, system health, Telegram webhook."""
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.api.common import ser
from syena.api.deps import authenticated
from syena.db import get_db, utcnow
from syena.execution.brokers import reset_wallet
from syena.market.calendar import to_ist
from syena.market.data import KiteMarketData
from syena.market import kite as kite_session
from syena.models import AICall, Trade, TradeStatus
from syena.monitor import approve_sell, reject_sell, status_text
from syena.notify import telegram
from syena.services import mode as mode_service

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(authenticated)])
public_router = APIRouter(tags=["telegram"])


@router.get("")
def get_all(db: Session = Depends(get_db)):
    return {"values": settings_store.get_all(db), "defaults": settings_store.DEFAULTS,
            "human_only": sorted(settings_store.HUMAN_ONLY_KEYS)}


@router.put("/{key}")
def put(key: str, value: dict | list = Body(...), db: Session = Depends(get_db)):
    if key not in settings_store.DEFAULTS:
        raise HTTPException(404, "unknown setting")
    try:
        return settings_store.put(db, key, value, actor="user")
    except ValueError as e:
        raise HTTPException(400, str(e))


class WalletIn(BaseModel):
    balance: float


@router.post("/paper-wallet/reset")
def wallet_reset(body: WalletIn, db: Session = Depends(get_db)):
    if body.balance <= 0:
        raise HTTPException(400, "balance must be positive")
    if db.scalar(select(func.count()).select_from(Trade).where(Trade.mode == "PAPER", Trade.status == TradeStatus.OPEN)):
        raise HTTPException(409, "close open paper positions before resetting the wallet")
    w = reset_wallet(db, body.balance)
    return ser(w)


@router.get("/ai-usage")
def ai_usage(days: int = 30, db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    by_day, by_purpose, by_model = {}, {}, {}
    for c in db.scalars(select(AICall).where(AICall.ts >= since)):
        d = to_ist(c.ts).date().isoformat()
        for bucket, key in ((by_day, d), (by_purpose, c.purpose), (by_model, c.model)):
            b = bucket.setdefault(key, {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "errors": 0})
            b["calls"] += 1
            b["cost_usd"] = round(b["cost_usd"] + c.cost_usd, 6)
            b["input_tokens"] += c.input_tokens
            b["output_tokens"] += c.output_tokens
            b["errors"] += 0 if c.success else 1
    recent = [ser(c, exclude=("prompt", "response_raw")) for c in db.scalars(select(AICall).order_by(AICall.id.desc()).limit(50))]
    return {"by_day": by_day, "by_purpose": by_purpose, "by_model": by_model, "recent": recent}


@router.get("/ai-calls/{call_id}")
def ai_call(call_id: int, db: Session = Depends(get_db)):
    c = db.get(AICall, call_id)
    if c is None:
        raise HTTPException(404, "not found")
    return ser(c)


@router.post("/telegram/test")
def telegram_test():
    if not telegram.enabled():
        raise HTTPException(409, "Telegram is not configured (SYENA_TELEGRAM_BOT_TOKEN / SYENA_TELEGRAM_CHAT_ID)")
    mid = telegram.send("✅ Syena test message")
    return {"sent": bool(mid)}


@public_router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    """Public, but every update must carry the webhook secret header and come from your chat."""
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(400, "bad body")
    if not telegram.verify_webhook(request.headers.get("x-telegram-bot-api-secret-token"), telegram.chat_id_of(update)):
        raise HTTPException(403, "forbidden")
    md = KiteMarketData(db) if kite_session.active_token(db) else None
    result = telegram.handle_update(
        db, update,
        approve_sell=lambda d, a, t: approve_sell(d, a, t, md),
        reject_sell=reject_sell,
        halt=lambda d, reason: mode_service.halt(d, reason, actor="telegram"),
        status_text=status_text,
    )
    return {"ok": True, "result": result}
