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
    instrument: str = "EQ"  # EQ (shares) | OPT (an option contract, bought; PAPER only)
    option: dict | None = None  # {underlying, expiry, strike, type, lot_size, ...} for OPT
    lots: int | None = None  # OPT: fixed number of lots (else sized from the options budget)
    max_hold_until: datetime | None = None  # override the style's holding period (options: before expiry)


BrokerFactory = "callable[[Session, str], Broker]"


def default_broker_factory(md=None):
    def factory(db: Session, mode: str) -> Broker:
        if mode == TradingMode.LIVE.value:
            from syena.market import kite

            return KiteBroker(lambda: kite.client(db), mode_service.current_mode)
        ltp_fn = (lambda s: md.ltp([s]).get(s)) if md is not None else None
        return PaperBroker(ltp_fn)

    return factory


def product_for(style: Style, instrument: str = "EQ") -> str:
    if instrument == "OPT":
        return "MIS" if style == Style.INTRADAY else "NRML"
    return "MIS" if style == Style.INTRADAY else "CNC"


def effective_mode(db: Session, intent: TradeIntent) -> tuple[str, Check]:
    if intent.instrument == "OPT":  # options are simulated only: never sent to Zerodha
        return "PAPER", Check("mode", True, "options are paper-traded only: simulated order")
    sys_mode = mode_service.current_mode(db)
    if sys_mode != TradingMode.LIVE:
        return "PAPER", Check("mode", True, "system is in PAPER mode: simulated order")
    if intent.source == "manual":
        return "LIVE", Check("mode", True, "system LIVE; manual order confirmed by you")
    algo = db.get(Algo, intent.algo_id) if intent.algo_id else None
    if algo is None or not algo.live_enabled:
        return "PAPER", Check("mode", True, "system LIVE but this strategy has not passed the LIVE gate: paper only")
    return "LIVE", Check("mode", True, f"system LIVE and strategy '{algo.name}' is LIVE-enabled")


def amo_allowed(db: Session, intent: TradeIntent) -> bool:
    """Signals while the market is closed are kept in Syena's own queue (any style, buy or short) and placed by
    Syena itself as normal orders once the market opens. Nothing is sent to the broker until then."""
    return bool(settings_store.get(db, "amo")["enabled"])


def base_checks(db: Session, intent: TradeIntent, mode: str, now: datetime) -> tuple[list[Check], int, float, bool]:
    state = mode_service.get_state(db)
    holidays = settings_store.get(db, "holidays")
    styles = settings_store.get(db, "styles")
    sizing = settings_store.get(db, "sizing")
    checks = [Check("not_halted", not state.halted, "trading halted: " + (state.halt_reason or "") if state.halted else "kill switch off")]
    open_now = calendar.is_market_open(now, holidays)
    amo = not open_now and amo_allowed(db, intent)
    if open_now:
        checks.append(Check("market_hours", True, "market open"))
    elif amo:
        q = settings_store.get(db, "amo")
        way = "more than" if intent.side == "BUY" else "less than"
        checks.append(Check("market_hours", True, f"market closed: queued in Syena; it places the order itself after "
                                                  f"{q.get('place_after', '09:16')} at the next open, unless the price opens "
                                                  f"{q['max_gap_pct']:g}% {way} {intent.ref_price}"))
    else:
        checks.append(Check("market_hours", False, "market closed (hours/holiday); queueing for the next open is turned off "
                                                   "(Settings → amo → enabled)"))
    if intent.style == Style.INTRADAY and open_now:
        ok = calendar.before(now, styles["intraday_entry_cutoff"])
        checks.append(Check("intraday_cutoff", ok, f"cutoff {styles['intraday_entry_cutoff']} IST"))
    if intent.instrument == "OPT":
        from syena.execution.sizing import size_option

        ocfg = settings_store.get(db, "options")
        sz = size_option(intent.ref_price, int((intent.option or {}).get("lot_size") or 1), float(ocfg["min_premium"]),
                         float(ocfg["max_premium"]), int(ocfg["max_lots"]), intent.lots)
    else:
        sz = size_position(intent.ref_price, sizing["min_amount"], sizing["max_amount"])
    checks.append(sz.as_check())
    if intent.circuit:
        lo, hi = intent.circuit.get("lower"), intent.circuit.get("upper")
        near = (hi and intent.ref_price >= hi * 0.995) or (lo and intent.ref_price <= lo * 1.005)
        checks.append(Check("circuit_limit", not near, f"price {intent.ref_price} vs circuit {lo}-{hi}"))
    if sz.ok:
        if intent.instrument == "OPT":
            checks.extend(option_risk(db, intent, now))
        else:
            checks.extend(check_risk(db, mode, intent.symbol, sz.amount, now))
    return checks, sz.qty, sz.amount, amo


def option_risk(db: Session, intent: TradeIntent, now: datetime) -> list[Check]:
    """Options have their own limits, so they don't take the equity strategies' slots."""
    from syena.execution.risk import _ist_day_start

    ocfg = settings_store.get(db, "options")
    opens = list(db.scalars(select(Trade).where(Trade.instrument == "OPT", Trade.status.in_((TradeStatus.OPEN, TradeStatus.PENDING)))))
    today = [t for t in db.scalars(select(Trade).where(Trade.instrument == "OPT", Trade.created_at >= _ist_day_start(now)))
             if t.status != TradeStatus.FAILED]
    und = (intent.option or {}).get("underlying")
    same = [t for t in opens if (t.option or {}).get("underlying") == und]
    return [Check("options_enabled", bool(ocfg.get("enabled", True)), "option trading on" if ocfg.get("enabled", True) else "option trading is off (Settings → options)"),
            Check("max_open_options", len(opens) < int(ocfg["max_open"]), f"{len(opens)} open option positions / limit {ocfg['max_open']}"),
            Check("max_option_trades_per_day", len(today) < int(ocfg["max_per_day"]), f"{len(today)} option trades today / limit {ocfg['max_per_day']}"),
            Check("one_position_per_underlying", not same, f"already holding {same[0].stock} on {und}" if same else f"no open option on {und}")]


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
        if order.product == "NRML" or (order.trade_id and getattr(db.get(Trade, order.trade_id), "instrument", "EQ") == "OPT"):
            from syena.market.charges import option_leg_charges

            res.charges = option_leg_charges(order.side, res.filled_qty, res.fill_price).total
        else:
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
    checks, qty, amount, amo = base_checks(db, intent, mode, now)
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
    if intent.max_hold_until:
        max_hold = min(max_hold, intent.max_hold_until) if max_hold else intent.max_hold_until
    trade = Trade(instrument=intent.instrument, option=({**intent.option, "lots": qty // max(1, int(intent.option.get("lot_size") or 1))}
                                                       if intent.option else None),
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
    if amo:
        # Kept in Syena's queue: nothing goes to the broker now. At the open, execution/pending.py checks the gap
        # against this reference price and places a normal order itself.
        order = Order(idempotency_key=f"{key}-ENTRY", trade_id=trade.id, mode=mode, broker="kite" if mode == "LIVE" else "paper",
                      symbol=intent.symbol, side=intent.side, qty=qty, order_type="LIMIT", price=intent.ref_price,
                      product=product_for(intent.style, intent.instrument), purpose="ENTRY", variety="queued", status=OrderStatus.PLACED,
                      message="queued in Syena until the market opens")
        db.add(order)
        db.flush()
        db.add(OrderEvent(order_id=order.id, status="QUEUED", message="market closed: waiting in Syena's queue for the next open"))
        trade.status = TradeStatus.PENDING
        q = settings_store.get(db, "amo")
        checks.append(Check("queued", True, f"placed by Syena after {q.get('place_after', '09:16')} at the next open; cancelled if "
                                            f"the price gaps over {q['max_gap_pct']:g}% against the trade or nothing fills by "
                                            f"{q['cancel_unfilled_after']}"))
        d = _decision(db, intent, DecisionType.PLACED, mode,
                      f"QUEUED {'SHORT ' if intent.side == 'SELL' else ''}{intent.side} {qty} {intent.symbol}: Syena places it at the "
                      f"next market open (signal price {intent.ref_price})", checks, trade.id)
        trade.decision_id = d.id
        db.commit()
        events.publish(db, "decision", {"id": d.id, "type": d.type.value, "symbol": intent.symbol, "trade_id": trade.id})
        log.info("entry queued for the open", extra={"stock": intent.symbol, "side": intent.side, "mode": mode})
        return d
    variety = "regular"
    order = Order(idempotency_key=f"{key}-ENTRY", trade_id=trade.id, mode=mode, broker="kite" if mode == "LIVE" else "paper",
                  symbol=intent.symbol, side=intent.side, qty=qty, order_type=otype, price=limit,
                  product=product_for(intent.style, intent.instrument), purpose="ENTRY", variety=variety)
    db.add(order)
    db.flush()
    req = OrderRequest(order.idempotency_key, intent.symbol, intent.side, qty, order.product, "ENTRY",
                       intent.ref_price, otype, limit, variety, segment=intent.instrument)
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
        what = f"AMO queued for next open, limit {limit}" if amo else "awaiting fill"
        d = _decision(db, intent, DecisionType.PLACED, mode, f"PLACED {intent.side} {qty} {intent.symbol}: {what}", checks, trade.id)
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


def record_verdict(db: Session, intent: TradeIntent, dtype: DecisionType, summary: str, checks: list[Check]) -> Decision:
    """A decision that opens nothing: the AI said HOLD, or a step failed before the trade gate (REJECTED + reason)."""
    mode = mode_service.current_mode(db)
    d = _decision(db, intent, dtype, getattr(mode, "value", str(mode)), summary, checks)
    db.commit()
    events.publish(db, "decision", {"id": d.id, "type": dtype.value, "symbol": intent.symbol})
    log.info("decision recorded", extra={"stock": intent.symbol, "type": dtype.value, "summary": summary[:200]})
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
                  product=product_for(trade.style, trade.instrument or "EQ"), purpose="EXIT")
    db.add(order)
    db.flush()
    ocfg = settings_store.get(db, "orders")
    order.price = marketable_limit(intent.side, ref_price, ocfg["limit_buffer_bps"] * 3)  # wider buffer: exits must fill
    req = OrderRequest(key, trade.stock, intent.side, trade.qty, order.product, "EXIT", ref_price, "LIMIT", order.price,
                       entry_price=trade.entry_price if trade.side == "SELL" else None, segment=trade.instrument or "EQ")
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
