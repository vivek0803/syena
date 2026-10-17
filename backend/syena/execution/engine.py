"""Execution engine: every entry and exit goes through here and produces a Decision record.

Entry flow: effective mode -> base checks (halt, hours, cutoff, sizing, risk, circuit)
+ caller checks (confidence, technicals, market context, lessons, second opinion)
-> REJECTED decision with the exact failing check, or Trade + idempotent Order via the
broker for that mode -> PLACED decision.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import events, settings_store
from syena.db import utcnow
from syena.execution.brokers import Broker, KiteBroker, OrderRequest, OrderResult, PaperBroker, marketable_limit
from syena.execution.checks import Check, first_failure
from syena.execution.risk import check_risk, daily_loss_breached
from syena.execution.sizing import size_position
from syena.market import calendar
from syena.market.charges import leg_charges
from syena.models import (
    Algo, Decision, DecisionType, Order, OrderEvent, OrderStatus, Style, Trade, TradeStatus, TradingMode,
)
from syena.services import mode as mode_service

log = logging.getLogger("syena.engine")


@dataclass
class TradeIntent:
    symbol: str
    style: Style
    source: str  # algo | news | manual
    ref_price: float
    side: str = "BUY"
    algo_id: int | None = None
    signal_id: int | None = None
    confidence: float | None = None
    stop_loss: float | None = None
    target: float | None = None
    exit_condition: str | None = None
    algo_condition: str | None = None
    max_hold_days: int | None = None
    auto_sell: bool = False
    idempotency_key: str | None = None
    extra_checks: list[Check] = field(default_factory=list)
    reasoning: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    lessons_consulted: list = field(default_factory=list)
    lessons_applied: list = field(default_factory=list)
    lessons_would_apply: list = field(default_factory=list)
    similar_trades: list = field(default_factory=list)
    news_item_ids: list = field(default_factory=list)
    setup: dict = field(default_factory=dict)
    circuit: dict | None = None  # {"lower": x, "upper": y} when known
    order_type: str | None = None  # override; default from settings (LIMIT preferred)
    limit_price: float | None = None


BrokerFactory = "callable[[Session, str], Broker]"


def default_broker_factory(md=None):
    def factory(db: Session, mode: str) -> Broker:
        if mode == TradingMode.LIVE.value:
            from syena.market import kite

            return KiteBroker(lambda: kite.client(db), mode_service.current_mode)
        ltp_fn = (lambda s: md.ltp([s]).get(s)) if md is not None else None
        return PaperBroker(ltp_fn)

    return factory


def product_for(style: Style) -> str:
    return "MIS" if style == Style.INTRADAY else "CNC"


def effective_mode(db: Session, intent: TradeIntent) -> tuple[str, Check]:
    sys_mode = mode_service.current_mode(db)
    if sys_mode != TradingMode.LIVE:
        return "PAPER", Check("mode", True, "system is in PAPER mode: simulated order")
    if intent.source == "manual":
        return "LIVE", Check("mode", True, "system LIVE; manual order confirmed by you")
    algo = db.get(Algo, intent.algo_id) if intent.algo_id else None
    if algo is None or not algo.live_enabled:
        return "PAPER", Check("mode", True, "system LIVE but this strategy has not passed the LIVE gate: paper only")
    return "LIVE", Check("mode", True, f"system LIVE and strategy '{algo.name}' is LIVE-enabled")


def base_checks(db: Session, intent: TradeIntent, mode: str, now: datetime) -> tuple[list[Check], int, float]:
    state = mode_service.get_state(db)
    holidays = settings_store.get(db, "holidays")
    styles = settings_store.get(db, "styles")
    sizing = settings_store.get(db, "sizing")
    checks = [Check("not_halted", not state.halted, "trading halted: " + (state.halt_reason or "") if state.halted else "kill switch off")]
    open_now = calendar.is_market_open(now, holidays)
    checks.append(Check("market_hours", open_now, "market open" if open_now else "market closed (hours/holiday)"))
    if intent.style == Style.INTRADAY:
        ok = calendar.before(now, styles["intraday_entry_cutoff"])
        checks.append(Check("intraday_cutoff", ok, f"cutoff {styles['intraday_entry_cutoff']} IST"))
    sz = size_position(intent.ref_price, sizing["min_amount"], sizing["max_amount"])
    checks.append(sz.as_check())
    if intent.circuit:
        lo, hi = intent.circuit.get("lower"), intent.circuit.get("upper")
        near = (hi and intent.ref_price >= hi * 0.995) or (lo and intent.ref_price <= lo * 1.005)
        checks.append(Check("circuit_limit", not near, f"price {intent.ref_price} vs circuit {lo}-{hi}"))
    if sz.ok:
        checks.extend(check_risk(db, mode, intent.symbol, sz.amount, now))
    return checks, sz.qty, sz.amount


def _decision(db: Session, intent: TradeIntent, dtype: DecisionType, mode: str, summary: str,
              checks: list[Check], trade_id: int | None = None) -> Decision:
    d = Decision(
        type=dtype, mode=mode, source=intent.source, symbol=intent.symbol, side=intent.side,
        algo_id=intent.algo_id, signal_id=intent.signal_id, trade_id=trade_id, confidence=intent.confidence,
        summary=summary, reason_json=intent.reasoning, checks_json=[c.as_dict() for c in checks],
        inputs_json=intent.inputs, lessons_consulted=intent.lessons_consulted,
        lessons_applied=intent.lessons_applied, lessons_would_apply=intent.lessons_would_apply,
        similar_trades=intent.similar_trades,
    )
    db.add(d)
    db.flush()
    return d


def _record_order(db: Session, order: Order, res: OrderResult) -> None:
    order.status = OrderStatus(res.status) if res.status in OrderStatus.__members__ else OrderStatus.FAILED
    order.fill_price, order.filled_qty = res.fill_price, res.filled_qty
    order.broker_order_id, order.message, order.attempts = res.broker_order_id, res.message, res.attempts
    if res.status == "COMPLETE" and not res.charges and res.fill_price:
        res.charges = leg_charges(order.side, res.filled_qty, res.fill_price, order.product == "MIS").total
    order.charges = res.charges
    order.updated_at = utcnow()
    db.add(OrderEvent(order_id=order.id, status=order.status.value, message=res.message))


def _broker_error_fallback(db: Session) -> None:
    n = settings_store.get(db, "risk")["max_consecutive_broker_errors"]
    last = list(db.scalars(select(Order).where(Order.mode == "LIVE").order_by(Order.id.desc()).limit(n)))
    if len(last) == n and all(o.status == OrderStatus.FAILED for o in last):
        mode_service.fallback_to_paper(db, f"auto-fallback: {n} consecutive broker errors")


def enter(db: Session, intent: TradeIntent, broker_factory=None, now: datetime | None = None) -> Decision:
    now = now or utcnow()
    broker_factory = broker_factory or default_broker_factory()
    key = intent.idempotency_key or f"{intent.source}-{uuid.uuid4().hex}"
    existing = db.scalar(select(Order).where(Order.idempotency_key == f"{key}-ENTRY"))
    if existing is not None:  # idempotent: the same signal never produces a second order
        prior = db.scalar(select(Decision).where(Decision.trade_id == existing.trade_id))
        if prior:
            return prior

    mode, mode_check = effective_mode(db, intent)
    checks, qty, amount = base_checks(db, intent, mode, now)
    checks = [mode_check, *checks, *intent.extra_checks]
    fail = first_failure(checks)
    if fail:
        d = _decision(db, intent, DecisionType.REJECTED, mode, f"REJECTED by {fail.name}: {fail.detail}", checks)
        db.commit()
        events.publish(db, "decision", {"id": d.id, "type": "REJECTED", "symbol": intent.symbol})
        log.info("entry rejected", extra={"stock": intent.symbol, "algo_id": intent.algo_id, "check": fail.name})
        return d

    styles = settings_store.get(db, "styles")
    hold_days = intent.max_hold_days or (styles["swing_max_holding_days"] if intent.style == Style.SWING else 0)
    max_hold = calendar.add_trading_days(now, hold_days, settings_store.get(db, "holidays")) if hold_days else None
    trade = Trade(
        algo_id=intent.algo_id, signal_id=intent.signal_id, source=intent.source, mode=mode, style=intent.style,
        algo_condition=intent.algo_condition, stock=intent.symbol, side=intent.side, qty=qty, invested_amount=amount,
        exit_price_defined=intent.exit_condition, stop_loss=intent.stop_loss, target=intent.target,
        status=TradeStatus.PENDING, max_hold_until=max_hold, auto_sell=intent.auto_sell,
        news_item_ids=intent.news_item_ids or None, setup=intent.setup or None, created_at=now,
    )
    db.add(trade)
    db.flush()

    ocfg = settings_store.get(db, "orders")
    otype = intent.order_type or ("LIMIT" if ocfg["prefer_limit"] else "MARKET")
    limit = intent.limit_price or (marketable_limit(intent.side, intent.ref_price, ocfg["limit_buffer_bps"]) if otype == "LIMIT" else None)
    order = Order(idempotency_key=f"{key}-ENTRY", trade_id=trade.id, mode=mode, broker="kite" if mode == "LIVE" else "paper",
                  symbol=intent.symbol, side=intent.side, qty=qty, order_type=otype, price=limit,
                  product=product_for(intent.style), purpose="ENTRY")
    db.add(order)
    db.flush()
    req = OrderRequest(order.idempotency_key, intent.symbol, intent.side, qty, order.product, "ENTRY",
                       intent.ref_price, otype, limit)
    res = broker_factory(db, mode).place(db, req)
    _record_order(db, order, res)

    if res.status == "COMPLETE":
        trade.status, trade.entry_price, trade.charges = TradeStatus.OPEN, res.fill_price, res.charges
        trade.invested_amount = round(res.fill_price * res.filled_qty, 2)
        trade.last_price, trade.last_price_at = res.fill_price, now
        checks.append(Check("broker", True, res.message, {"order_id": res.broker_order_id}))
        d = _decision(db, intent, DecisionType.PLACED, mode,
                      f"PLACED {intent.side} {qty} {intent.symbol} @ {res.fill_price} ({mode})", checks, trade.id)
    elif res.status == "PLACED":
        trade.status = TradeStatus.PENDING
        checks.append(Check("broker", True, res.message, {"order_id": res.broker_order_id}))
        d = _decision(db, intent, DecisionType.PLACED, mode, f"PLACED {intent.side} {qty} {intent.symbol}: awaiting fill", checks, trade.id)
    else:
        trade.status = TradeStatus.FAILED
        checks.append(Check("broker", False, res.message))
        d = _decision(db, intent, DecisionType.REJECTED, mode, f"REJECTED by broker: {res.message}", checks, trade.id)
    trade.decision_id = d.id
    db.commit()
    if mode == "LIVE" and res.status == "FAILED":
        _broker_error_fallback(db)
    events.publish(db, "decision", {"id": d.id, "type": d.type.value, "symbol": intent.symbol, "trade_id": trade.id})
    log.info("entry processed", extra={"stock": intent.symbol, "algo_id": intent.algo_id, "result": d.type.value, "mode": mode})
    return d


def exit_trade(db: Session, trade: Trade, reason: str, ref_price: float, actor: str = "system",
               broker_factory=None, now: datetime | None = None, detail: dict | None = None) -> Decision:
    """Close an open trade. Exits of LIVE trades go to Zerodha even if the system is now PAPER:
    they are protective closing orders for positions that already exist (spec: open live positions stay monitored)."""
    now = now or utcnow()
    broker_factory = broker_factory or default_broker_factory()
    intent = TradeIntent(symbol=trade.stock, style=trade.style, source=trade.source, ref_price=ref_price,
                         side="SELL" if trade.side == "BUY" else "BUY", algo_id=trade.algo_id, signal_id=trade.signal_id,
                         reasoning={"exit_reason": reason, "actor": actor, **(detail or {})})
    if trade.status != TradeStatus.OPEN:
        return _decision(db, intent, DecisionType.SKIPPED, trade.mode, f"exit skipped: trade is {trade.status.value}", [])
    prior = list(db.scalars(select(Order).where(Order.trade_id == trade.id, Order.purpose == "EXIT").order_by(Order.id)))
    if any(o.status == OrderStatus.COMPLETE for o in prior):
        return _decision(db, intent, DecisionType.SKIPPED, trade.mode, "exit already completed", [])
    if any(o.status == OrderStatus.PLACED for o in prior):
        return _decision(db, intent, DecisionType.HELD, trade.mode, "exit order already working at broker", [], trade.id)
    key = f"trade{trade.id}-EXIT-{len(prior) + 1}"  # new key per attempt: a rejected exit can be retried
    order = Order(idempotency_key=key, trade_id=trade.id, mode=trade.mode, broker="kite" if trade.mode == "LIVE" else "paper",
                  symbol=trade.stock, side=intent.side, qty=trade.qty, order_type="LIMIT",
                  product=product_for(trade.style), purpose="EXIT")
    db.add(order)
    db.flush()
    ocfg = settings_store.get(db, "orders")
    order.price = marketable_limit(intent.side, ref_price, ocfg["limit_buffer_bps"] * 3)  # wider buffer: exits must fill
    req = OrderRequest(key, trade.stock, intent.side, trade.qty, order.product, "EXIT", ref_price, "LIMIT", order.price)
    res = broker_factory(db, trade.mode).place(db, req)
    _record_order(db, order, res)
    checks = [Check("exit_reason", True, reason), Check("broker", res.status == "COMPLETE", res.message)]
    if res.status != "COMPLETE":
        d = _decision(db, intent, DecisionType.HELD, trade.mode, f"exit order not filled: {res.message}", checks, trade.id)
        db.commit()
        return d
    trade.exit_price_executed = res.fill_price
    trade.charges = round((trade.charges or 0) + res.charges, 2)
    sign = 1 if trade.side == "BUY" else -1
    trade.profit_loss = round(sign * (res.fill_price - trade.entry_price) * trade.qty - trade.charges, 2)
    trade.exit_reason, trade.status, trade.closed_at = reason, TradeStatus.CLOSED, now
    trade.last_price, trade.last_price_at = res.fill_price, now
    d = _decision(db, intent, DecisionType.EXITED, trade.mode,
                  f"EXITED {trade.stock} @ {res.fill_price}: {reason}; P&L ₹{trade.profit_loss:,.2f}", checks, trade.id)
    db.commit()
    events.publish(db, "trade_closed", {"trade_id": trade.id, "symbol": trade.stock, "pnl": trade.profit_loss})
    log.info("trade closed", extra={"stock": trade.stock, "algo_id": trade.algo_id, "reason": reason, "pnl": trade.profit_loss})
    if trade.mode == "LIVE" and daily_loss_breached(db, "LIVE"):
        mode_service.halt(db, "max daily loss breached (LIVE)", actor="system")
    return d


def mark_to_market(db: Session, prices: dict[str, float], now: datetime | None = None) -> int:
    now = now or utcnow()
    n = 0
    for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN)):
        p = prices.get(t.stock)
        if p:
            t.last_price, t.last_price_at = p, now
            n += 1
    db.commit()
    return n


def unrealised(t: Trade) -> float | None:
    if t.status != TradeStatus.OPEN or not t.last_price or not t.entry_price:
        return None
    sign = 1 if t.side == "BUY" else -1
    return round(sign * (t.last_price - t.entry_price) * t.qty - (t.charges or 0), 2)


def stale_pending(db: Session, older_than: timedelta = timedelta(minutes=30)) -> list[Trade]:
    cutoff = utcnow() - older_than
    return list(db.scalars(select(Trade).where(Trade.status == TradeStatus.PENDING, Trade.created_at < cutoff)))
