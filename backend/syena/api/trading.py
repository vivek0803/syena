"""Dashboard, decisions, trades/positions, orders, CSV exports, live event stream, Zerodha connection."""
import asyncio
import csv
import io
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.ai import llm
from syena.api.common import market_data, ser
from syena.api.deps import AuthContext, authenticated
from syena.db import SessionLocal, get_db, utcnow
from syena.execution import engine
from syena.execution.brokers import get_wallet
from syena.execution.risk import daily_pnl
from syena.market import calendar
from syena.market import kite as kite_session
from syena.models import (
    AICall, Alert, Decision, DecisionType, Event, LessonApplication, Lesson, NewsAnalysis, NewsItem, Order, OrderEvent,
    Signal, Trade, TradeReview, TradeStatus, WorkerHeartbeat,
)
from syena.notify import telegram
from syena.portfolio.sources import angel_status
from syena.services import mode as mode_service

router = APIRouter(prefix="/api", tags=["trading"], dependencies=[Depends(authenticated)])
public_router = APIRouter(tags=["kite"])


def trade_row(t: Trade) -> dict:
    return ser(t, extra={"unrealised": engine.unrealised(t)})


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    st = mode_service.get_state(db)
    now = utcnow()
    start = calendar.to_ist(now).replace(hour=0, minute=0, second=0, microsecond=0)
    pnl = {}
    for m in ("PAPER", "LIVE"):
        total = db.scalar(select(func.coalesce(func.sum(Trade.profit_loss), 0.0)).where(Trade.mode == m, Trade.status == TradeStatus.CLOSED)) or 0
        pnl[m] = {"today": daily_pnl(db, m), "realised_all_time": round(total, 2)}
    opens = list(db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN).order_by(Trade.created_at.desc())))
    decisions_today = dict((k.value, v) for k, v in db.execute(
        select(Decision.type, func.count()).where(Decision.ts >= start).group_by(Decision.type)).all())
    recent = list(db.scalars(select(Decision).order_by(Decision.ts.desc(), Decision.id.desc()).limit(12)))
    w = get_wallet(db)
    heartbeats = {h.name: {"ts": h.ts.isoformat(), "age_s": int((now - h.ts).total_seconds()), "detail": h.detail}
                  for h in db.scalars(select(WorkerHeartbeat))}
    fast = heartbeats.get("fast_loop")
    ai_today = db.scalar(select(func.coalesce(func.sum(AICall.cost_usd), 0.0)).where(AICall.ts >= start)) or 0
    return {
        "mode": {"mode": st.mode.value, "halted": st.halted, "halt_reason": st.halt_reason},
        "pnl": pnl,
        "wallet": {"cash": round(w.cash, 2), "starting_balance": w.starting_balance,
                   "invested": round(sum(t.invested_amount for t in opens if t.mode == "PAPER"), 2)},
        "open_positions": [trade_row(t) for t in opens],
        "decisions_today": decisions_today,
        "recent_decisions": [ser(d, exclude=("reason_json", "inputs_json", "similar_trades")) for d in recent],
        "kite": kite_session.token_status(db),
        "angel": angel_status(db),
        "market_open": calendar.is_market_open(now, settings_store.get(db, "holidays")),
        "health": {
            "db": "ok",
            "worker": "ok" if fast and fast["age_s"] < 180 else "stale" if fast else "not running",
            "heartbeats": heartbeats,
            "ai_configured": llm.available(),
            "ai_spend_today_usd": round(ai_today, 4),
            "ai_budget_usd": settings_store.get(db, "ai_budget")["daily_usd"],
            "telegram": telegram.enabled(),
        },
        "alerts": [ser(a) for a in db.scalars(select(Alert).order_by(Alert.id.desc()).limit(8))],
    }


# ---------------------------------------------------------------- decisions
@router.get("/decisions")
def decisions(type: str | None = None, symbol: str | None = None, source: str | None = None, algo_id: int | None = None,
              mode: str | None = None, before_id: int | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = select(Decision)
    if type:
        q = q.where(Decision.type == DecisionType(type.upper()))
    if symbol:
        q = q.where(Decision.symbol == symbol.upper())
    if source:
        q = q.where(Decision.source == source)
    if algo_id:
        q = q.where(Decision.algo_id == algo_id)
    if mode:
        q = q.where(Decision.mode == mode.upper())
    if before_id:
        q = q.where(Decision.id < before_id)
    rows = db.scalars(q.order_by(Decision.ts.desc(), Decision.id.desc()).limit(min(limit, 500)))
    return [ser(d, exclude=("inputs_json", "similar_trades")) for d in rows]


@router.get("/decisions/{decision_id}")
def decision_detail(decision_id: int, db: Session = Depends(get_db)):
    d = db.get(Decision, decision_id)
    if d is None:
        raise HTTPException(404, "not found")
    trade = db.get(Trade, d.trade_id) if d.trade_id else None
    orders = list(db.scalars(select(Order).where(Order.trade_id == trade.id))) if trade else []
    sig = db.get(Signal, d.signal_id) if d.signal_id else None
    news, ai_calls = [], []
    if trade and trade.news_item_ids:
        news = [ser(n) for n in db.scalars(select(NewsItem).where(NewsItem.id.in_(trade.news_item_ids)))]
    if sig and sig.source == "news":
        for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.stage == "analysis").order_by(NewsAnalysis.id.desc()).limit(300)):
            if (a.result or {}).get("signal_id") == sig.id and a.ai_call_id:
                c = db.get(AICall, a.ai_call_id)
                ai_calls.append(ser(c))
                if not news:
                    news = [{"id": n["id"], "title": n["title"]} for n in ((a.result or {}).get("inputs") or {}).get("news", [])]
    apps = [ser(a, extra={"code": db.get(Lesson, a.lesson_id).code}) for a in db.scalars(select(LessonApplication).where(LessonApplication.decision_id == d.id))]
    review = db.scalar(select(TradeReview).where((TradeReview.decision_id == d.id) | ((TradeReview.trade_id == d.trade_id) & (TradeReview.trade_id.is_not(None)))))
    return {"decision": ser(d), "trade": trade_row(trade) if trade else None,
            "orders": [ser(o, extra={"events": [ser(e) for e in db.scalars(select(OrderEvent).where(OrderEvent.order_id == o.id))]}) for o in orders],
            "signal": ser(sig), "news": news, "ai_calls": ai_calls, "lesson_applications": apps, "review": ser(review)}


# ---------------------------------------------------------------- trades / positions / orders
@router.get("/trades")
def trades(status: str | None = None, mode: str | None = None, source: str | None = None, symbol: str | None = None,
           limit: int = 200, db: Session = Depends(get_db)):
    q = select(Trade)
    if status:
        q = q.where(Trade.status == TradeStatus(status.upper()))
    if mode:
        q = q.where(Trade.mode == mode.upper())
    if source:
        q = q.where(Trade.source == source)
    if symbol:
        q = q.where(Trade.stock == symbol.upper())
    return [trade_row(t) for t in db.scalars(q.order_by(Trade.id.desc()).limit(min(limit, 1000)))]


@router.get("/positions")
def positions(db: Session = Depends(get_db)):
    return [trade_row(t) for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN).order_by(Trade.created_at.desc()))]


class TradePatch(BaseModel):
    auto_sell: bool | None = None
    stop_loss: float | None = None
    target: float | None = None


@router.patch("/trades/{trade_id}")
def patch_trade(trade_id: int, body: TradePatch, db: Session = Depends(get_db)):
    t = db.get(Trade, trade_id)
    if t is None or t.status != TradeStatus.OPEN:
        raise HTTPException(404, "open trade not found")
    if body.auto_sell is not None:
        t.auto_sell = body.auto_sell
    if body.stop_loss is not None:
        t.stop_loss = body.stop_loss
    if body.target is not None:
        t.target = body.target
    db.commit()
    return trade_row(t)


@router.post("/trades/{trade_id}/exit")
def exit_now(trade_id: int, md=Depends(market_data), db: Session = Depends(get_db)):
    t = db.get(Trade, trade_id)
    if t is None or t.status != TradeStatus.OPEN:
        raise HTTPException(404, "open trade not found")
    price = md.ltp([t.stock]).get(t.stock) or t.last_price
    d = engine.exit_trade(db, t, "manual_exit", price, actor="user", broker_factory=engine.default_broker_factory(md))
    return ser(d)


@router.get("/orders")
def orders(limit: int = 200, db: Session = Depends(get_db)):
    out = []
    for o in db.scalars(select(Order).order_by(Order.id.desc()).limit(min(limit, 1000))):
        out.append(ser(o, extra={"events": [ser(e) for e in db.scalars(select(OrderEvent).where(OrderEvent.order_id == o.id))]}))
    return out


def _csv(rows: list[dict], name: str) -> StreamingResponse:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in r.items()})
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/export/trades.csv")
def export_trades(db: Session = Depends(get_db)):
    return _csv([trade_row(t) for t in db.scalars(select(Trade).order_by(Trade.id))], "syena-trades.csv")


@router.get("/export/decisions.csv")
def export_decisions(db: Session = Depends(get_db)):
    return _csv([ser(d) for d in db.scalars(select(Decision).order_by(Decision.id))], "syena-decisions.csv")


@router.get("/performance")
def performance(db: Session = Depends(get_db)):
    """Paper vs live comparison per algo (spec 15)."""
    rows = {}
    for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.CLOSED)):
        key = (t.algo_id, t.mode)
        r = rows.setdefault(key, {"algo_id": t.algo_id, "mode": t.mode, "trades": 0, "wins": 0, "pnl": 0.0})
        r["trades"] += 1
        r["wins"] += 1 if (t.profit_loss or 0) > 0 else 0
        r["pnl"] += t.profit_loss or 0
    out = []
    for r in rows.values():
        r["win_rate"] = round(r["wins"] / r["trades"], 3) if r["trades"] else None
        r["pnl"] = round(r["pnl"], 2)
        out.append(r)
    return sorted(out, key=lambda r: (r["algo_id"] or 0, r["mode"]))


# ---------------------------------------------------------------- live stream (SSE)
@router.get("/stream")
async def stream(request: Request, since: int | None = None):
    last = since
    if last is None:
        with SessionLocal() as db:
            last = db.scalar(select(func.max(Event.id))) or 0

    async def gen():
        nonlocal last
        idle = 0
        while not await request.is_disconnected():
            with SessionLocal() as db:
                rows = list(db.scalars(select(Event).where(Event.id > last).order_by(Event.id).limit(100)))
            for e in rows:
                last = e.id
                yield f"id: {e.id}\nevent: {e.kind}\ndata: {json.dumps(e.payload or {}, default=str)}\n\n"
            idle = 0 if rows else idle + 1
            if idle and idle % 8 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- Zerodha connect
@router.post("/kite/login-url")
def kite_login_url(db: Session = Depends(get_db)):
    try:
        return {"url": kite_session.login_url(db)}
    except kite_session.KiteNotConnected as e:
        raise HTTPException(409, str(e))


@router.get("/kite/status")
def kite_status(db: Session = Depends(get_db)):
    return kite_session.token_status(db)


@router.post("/kite/disconnect")
def kite_disconnect(ctx: AuthContext = Depends(authenticated), db: Session = Depends(get_db)):
    row = kite_session.active_token(db)
    if row:
        row.revoked = True
        db.commit()
    mode_service.fallback_to_paper(db, "auto-fallback: Zerodha disconnected", ctx.ip)
    return kite_session.token_status(db)


@public_router.get("/kite/callback")
def kite_callback(request_token: str | None = None, nonce: str | None = None, status: str | None = None,
                  db: Session = Depends(get_db)):
    """Zerodha redirects here. Protected by the one-time nonce we put in redirect_params."""
    if not kite_session.consume_nonce(db, nonce):
        return RedirectResponse("/?kite=invalid", status_code=303)
    if status != "success" or not request_token:
        return RedirectResponse("/?kite=cancelled", status_code=303)
    try:
        kite_session.complete_login(db, request_token)
    except Exception:
        return RedirectResponse("/?kite=error", status_code=303)
    return RedirectResponse("/?kite=connected", status_code=303)
