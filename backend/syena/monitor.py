"""Position monitor (spec 9) and periodic summaries. Runs in the always-on worker, independent of the browser.

Default behaviour: conditions produce a Telegram SELL alert with Approve/Reject. Positions with auto-sell on
(default for algo/news trades, off for manual investments; toggle per position) exit automatically.
Intraday positions are always squared off before 15:15.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import events, settings_store
from syena.db import utcnow
from syena.execution import engine
from syena.execution.risk import daily_pnl
from syena.market import calendar
from syena.market import kite as kite_session
from syena.models import (Alert, Decision, DecisionType, Lesson, NewsAnalysis, Style, Trade, TradeReview, TradeStatus,
                          TradingMode)
from syena.notify import telegram
from syena.services import mode as mode_service

log = logging.getLogger("syena.monitor")


def _exit_or_alert(db: Session, t: Trade, reason: str, detail: str, price: float, broker_factory=None) -> str:
    if t.snoozed_until and t.snoozed_until > utcnow() and reason not in ("stop_loss", "square_off"):
        return "snoozed"
    if t.auto_sell or reason == "square_off":
        d = engine.exit_trade(db, t, reason, price, actor="auto", broker_factory=broker_factory, detail={"detail": detail})
        if d.type == DecisionType.EXITED:
            telegram.notify(db, "info", f"🔁 Auto-exit {telegram.esc(t.stock)} [{t.mode}]: {reason}. P&L ₹{t.profit_loss:,.2f}",
                            t.stock, t.id)
        return "exited"
    a = telegram.sell_alert(db, t, reason, detail)
    if a:
        events.publish(db, "alert", {"kind": "sell", "symbol": t.stock, "trade_id": t.id, "reason": reason})
    return "alerted" if a else "already_alerted"


def check_price_levels(db: Session, prices: dict[str, float], now: datetime | None = None, broker_factory=None) -> list[str]:
    """Fast loop (every minute): stop-loss / target / square-off on live prices."""
    now = now or utcnow()
    engine.mark_to_market(db, prices, now)
    styles = settings_store.get(db, "styles")
    out = []
    for t in list(db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN))):
        p = prices.get(t.stock)
        if not p:
            continue
        if t.style == Style.INTRADAY and not calendar.before(now, styles["intraday_square_off"]):
            out.append(_exit_or_alert(db, t, "square_off", f"intraday square-off at {styles['intraday_square_off']}", p, broker_factory))
        elif t.stop_loss and (p >= t.stop_loss if t.side == "SELL" else p <= t.stop_loss):  # shorts: stop is above
            out.append(_exit_or_alert(db, t, "stop_loss", f"LTP {p} {'≥' if t.side == 'SELL' else '≤'} stop {t.stop_loss}", p, broker_factory))
        elif t.target and (p <= t.target if t.side == "SELL" else p >= t.target):
            out.append(_exit_or_alert(db, t, "target", f"LTP {p} {'≤' if t.side == 'SELL' else '≥'} target {t.target}", p, broker_factory))
    return out


def scan(db: Session, md, now: datetime | None = None, broker_factory=None) -> dict:
    """30-minute scan: price levels, holding period, swing gaps, exit conditions, fresh negative news."""
    now = now or utcnow()
    opens = list(db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN)))
    if not opens:
        return {"open": 0}
    prices = md.ltp(sorted({t.stock for t in opens}))
    results = check_price_levels(db, prices, now, broker_factory)
    styles = settings_store.get(db, "styles")
    mon = settings_store.get(db, "monitor")
    for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN)):
        p = prices.get(t.stock) or t.last_price
        if not p:
            continue
        if t.max_hold_until and now >= t.max_hold_until:
            results.append(_exit_or_alert(db, t, "max_holding_period", f"held since {t.created_at:%d %b}", p, broker_factory))
            continue
        if t.style == Style.SWING and (t.instrument or "EQ") == "EQ" and calendar.to_ist(t.created_at).date() < calendar.to_ist(now).date():
            day = md.candles(t.stock, "day", now - timedelta(days=5), now)
            if len(day) >= 2:
                gap = (float(day["open"].iloc[-1]) / float(day["close"].iloc[-2]) - 1) * 100
                if gap <= -styles["swing_gap_down_exit_pct"]:
                    results.append(_exit_or_alert(db, t, "gap_down", f"opened {gap:.1f}% below previous close", p, broker_factory))
                    continue
        since = max(t.created_at, now - timedelta(minutes=mon["scan_minutes"] + 5))
        sym, bearish_pos = t.stock, t.side == "SELL"
        if (t.instrument or "EQ") == "OPT":  # an option: watch news on the underlying; a put profits from bad news
            sym, bearish_pos = (t.option or {}).get("underlying") or t.stock, (t.option or {}).get("type") == "PE"
        thr = mon["negative_news_sentiment"]
        bad = [a for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.stage == "analysis", NewsAnalysis.created_at >= since))
               if sym in (a.symbols or []) and a.sentiment is not None
               and (a.sentiment >= -thr if bearish_pos else a.sentiment <= thr)]
        if bad:
            results.append(_exit_or_alert(db, t, "positive_news" if bearish_pos else "negative_news",
                                          f"{bad[-1].summary or 'material news against the position'}", p, broker_factory))
    return {"open": len(opens), "actions": results}


def handle_news_sell_signals(db: Session, since: datetime, prices: dict[str, float]) -> int:
    from syena.models import Event

    n = 0
    for e in db.scalars(select(Event).where(Event.kind == "news_sell_signal", Event.ts >= since)):
        t = db.get(Trade, (e.payload or {}).get("trade_id"))
        if t and t.status == TradeStatus.OPEN:
            _exit_or_alert(db, t, "news_sell_signal", f"AI news signal SELL (signal #{e.payload.get('signal_id')})",
                           prices.get(t.stock) or t.last_price or t.entry_price)
            n += 1
    return n


# ---------------------------------------------------------------- Telegram callbacks
def approve_sell(db: Session, alert: Alert, trade: Trade, md=None) -> str:
    price = trade.last_price or trade.entry_price
    if md is not None:
        price = md.ltp([trade.stock]).get(trade.stock) or price
    d = engine.exit_trade(db, trade, (alert.payload or {}).get("reason", "approved_alert"), price, actor="user",
                          broker_factory=engine.default_broker_factory(md))
    return d.summary


def reject_sell(db: Session, alert: Alert, trade: Trade) -> None:
    trade.snoozed_until = utcnow() + timedelta(minutes=settings_store.get(db, "monitor")["snooze_minutes"])
    db.add(Decision(type=DecisionType.HELD, mode=trade.mode, source=trade.source, symbol=trade.stock, trade_id=trade.id,
                    algo_id=trade.algo_id, summary=f"HELD {trade.stock}: you rejected the SELL alert ({alert.message})",
                    reason_json={"alert_id": alert.id}, checks_json=[]))
    db.commit()


def status_text(db: Session) -> str:
    st = mode_service.get_state(db)
    lines = [f"<b>Syena</b> mode: <b>{st.mode.value}</b>{' (HALTED)' if st.halted else ''}"]
    for m in ("PAPER", "LIVE"):
        p = daily_pnl(db, m)
        n = db.scalar(select(func.count()).select_from(Trade).where(Trade.mode == m, Trade.status == TradeStatus.OPEN)) or 0
        lines.append(f"{m}: {n} open, today ₹{p['total']:,.0f} (realised ₹{p['realised']:,.0f})")
    k = kite_session.token_status(db)
    lines.append(f"Zerodha: {'connected' if k['connected'] else 'NOT connected'}")
    return "\n".join(lines)


# ---------------------------------------------------------------- token, summaries
def token_check(db: Session) -> None:
    if kite_session.active_token(db) is None:
        if mode_service.fallback_to_paper(db, "auto-fallback: Kite token expired"):
            telegram.notify(db, "token", "⚠️ Zerodha token expired: switched to <b>PAPER</b>. Reconnect from the dashboard.")
        last = db.scalar(select(Alert).where(Alert.kind == "token").order_by(Alert.id.desc()))
        if last is None or last.created_at < utcnow() - timedelta(hours=20):
            telegram.notify(db, "token", "🔑 Zerodha is not connected today. Open Syena and tap <b>Connect Zerodha</b>.")


def daily_summary(db: Session, now: datetime | None = None) -> str:
    now = now or utcnow()
    start = calendar.to_ist(now).replace(hour=0, minute=0, second=0, microsecond=0)
    lines = [f"📊 <b>Daily summary</b> {calendar.to_ist(now):%d %b %Y}"]
    for m in ("PAPER", "LIVE"):
        p = daily_pnl(db, m, now)
        closed = db.scalar(select(func.count()).select_from(Trade).where(Trade.mode == m, Trade.closed_at >= start)) or 0
        lines.append(f"{m}: P&L ₹{p['total']:,.0f} (realised ₹{p['realised']:,.0f}), {closed} closed")
    counts = dict(db.execute(select(Decision.type, func.count()).where(Decision.ts >= start).group_by(Decision.type)).all())
    lines.append("Decisions: " + ", ".join(f"{k.value.lower()} {v}" for k, v in counts.items()) if counts else "No decisions today")
    rej = list(db.scalars(select(Decision).where(Decision.ts >= start, Decision.type == DecisionType.REJECTED).limit(5)))
    for d in rej:
        lines.append(f"• {telegram.esc(d.symbol)}: {telegram.esc(d.summary[:90])}")
    text = "\n".join(lines)
    telegram.notify(db, "summary", text)
    return text


def weekly_digest(db: Session, now: datetime | None = None) -> str:
    now = now or utcnow()
    since = now - timedelta(days=7)
    cls = dict(db.execute(select(TradeReview.classification, func.count()).where(TradeReview.created_at >= since)
                          .group_by(TradeReview.classification)).all())
    proposed = list(db.scalars(select(Lesson).where(Lesson.status.in_(("PROPOSED", "TESTING")), Lesson.created_at >= since)))
    awaiting = list(db.scalars(select(Lesson).where(Lesson.status == "TESTING")))
    lines = ["🧠 <b>Weekly learning digest</b>", "Review outcomes: " + (", ".join(f"{k} {v}" for k, v in sorted(cls.items(), key=lambda x: -x[1])) or "none")]
    lines.append(f"New lessons this week: {len(proposed)}")
    for l in awaiting[:5]:
        ok = (l.validation or {}).get("passed")
        lines.append(f"• {l.code} [{'validated ✓' if ok else 'not validated'}] {telegram.esc(l.text[:100])}")
    if awaiting:
        lines.append("Approve or reject on the Learning page.")
    text = "\n".join(lines)
    telegram.notify(db, "summary", text)
    return text
