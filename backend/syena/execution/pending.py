"""Follow-up for entries that did not fill immediately: Syena's after-hours queue and live orders left open.

Runs every minute (worker job `pending_orders`):
- QUEUED entries (signals that came while the market was closed) are placed by Syena itself, as normal orders,
  from `amo.place_after` (09:16) at the next open: paper or live, buy or short, any style. If the price opened more
  than `amo.max_gap_pct` against the trade, Syena keeps waiting for it to come back until the cutoff. Stop-loss and
  target move with the actual fill so the risk stays what the signal planned.
- LIVE entries are reconciled with Zerodha's order history.
- Entries not placed/filled by `amo.cancel_unfilled_after` (10:00 IST) are cancelled with the reason.
- When trading is halted (kill switch), every pending entry is cancelled.
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import events, settings_store
from syena.db import utcnow
from syena.execution.brokers import OrderRequest, OrderResult, PaperBroker, marketable_limit
from syena.execution.engine import _record_order
from syena.market import calendar
from syena.models import Order, OrderEvent, OrderStatus, Trade, TradeStatus
from syena.services import mode as mode_service

log = logging.getLogger("syena.pending")


def _pending_entries(db: Session) -> list[Order]:
    return list(db.scalars(select(Order).where(Order.purpose == "ENTRY", Order.status == OrderStatus.PLACED).order_by(Order.id)))


def _notify(db: Session, text: str, trade: Trade) -> None:
    try:
        from syena.notify import telegram

        telegram.notify(db, "amo", text, symbol=trade.stock, trade_id=trade.id)
    except Exception as e:  # alerts must never break order handling
        log.info("amo notify failed", extra={"error": str(e)})


def _opened(db: Session, order: Order, trade: Trade, res: OrderResult, now: datetime) -> None:
    _record_order(db, order, res)
    trade.status, trade.entry_price, trade.charges = TradeStatus.OPEN, res.fill_price, res.charges
    trade.qty = res.filled_qty or trade.qty
    trade.invested_amount = round(res.fill_price * trade.qty, 2)
    trade.last_price, trade.last_price_at = res.fill_price, now
    db.commit()
    events.publish(db, "trade_opened", {"trade_id": trade.id, "symbol": trade.stock, "price": res.fill_price})
    log.info("pending entry filled", extra={"stock": trade.stock, "mode": order.mode, "variety": order.variety,
                                             "price": res.fill_price})
    _notify(db, f"✅ {order.mode} {'AMO ' if order.variety == 'amo' else ''}buy filled: {trade.qty} {trade.stock} @ ₹{res.fill_price}", trade)


def _closed_unfilled(db: Session, order: Order, trade: Trade, status: OrderStatus, why: str) -> None:
    order.status, order.message, order.updated_at = status, why, utcnow()
    db.add(OrderEvent(order_id=order.id, status=status.value, message=why))
    trade.status = TradeStatus.FAILED
    trade.exit_reason = why[:64]
    db.commit()
    events.publish(db, "order_cancelled", {"trade_id": trade.id, "symbol": trade.stock, "reason": why})
    log.info("pending entry closed unfilled", extra={"stock": trade.stock, "mode": order.mode, "reason": why})
    _notify(db, f"⏹ {order.mode} {'AMO ' if order.variety == 'amo' else ''}buy for {trade.stock} not filled: {why}", trade)


def _cancel_live(kite, order: Order) -> None:
    try:
        hist = kite.order_history(order.broker_order_id)
        variety = (hist[-1].get("variety") if hist else None) or order.variety
        kite.cancel_order(variety=variety, order_id=order.broker_order_id)
    except Exception as e:
        log.warning("live cancel failed", extra={"stock": order.symbol, "error": str(e)})
        raise


def _place_queued(db: Session, order: Order, trade: Trade, md, now: datetime, cfg: dict, past_cutoff: bool,
                  broker_factory, out: dict) -> None:
    quote = None
    if md is not None:
        try:
            quote = md.ltp([order.symbol]).get(order.symbol)
        except Exception as e:
            log.warning("queued order quote failed", extra={"stock": order.symbol, "error": str(e)})
    ref, gap = float(order.price or 0), float(cfg["max_gap_pct"])
    if not quote:
        if past_cutoff:
            _closed_unfilled(db, order, trade, OrderStatus.CANCELLED, f"no live price by {cfg['cancel_unfilled_after']}")
            out["cancelled"] += 1
        else:
            out["still_open"] += 1
        return
    moved = (quote / ref - 1) * 100 if ref else 0.0
    against = moved > gap if order.side == "BUY" else moved < -gap
    if against:
        if past_cutoff:
            _closed_unfilled(db, order, trade, OrderStatus.CANCELLED,
                             f"price stayed {moved:+.1f}% from the signal price {ref} (limit {gap:g}%) until {cfg['cancel_unfilled_after']}")
            out["cancelled"] += 1
        else:
            out["still_open"] += 1  # gapped too far: wait for it to come back
        return
    styles = settings_store.get(db, "styles")
    if order.product == "MIS" and not calendar.before(now, styles["intraday_entry_cutoff"]):
        _closed_unfilled(db, order, trade, OrderStatus.CANCELLED, f"intraday entry cutoff {styles['intraday_entry_cutoff']} passed")
        out["cancelled"] += 1
        return
    ocfg = settings_store.get(db, "orders")
    limit = marketable_limit(order.side, quote, ocfg["limit_buffer_bps"])
    req = OrderRequest(order.idempotency_key, order.symbol, order.side, order.qty, order.product, "ENTRY", quote, "LIMIT", limit)
    res = broker_factory(db, order.mode).place(db, req)
    order.variety, order.price = "fromq", limit  # now an ordinary order at the broker (still cancelled at the cutoff)
    db.add(OrderEvent(order_id=order.id, status="SENT", message=f"Syena placed the queued order at the open: LTP {quote}, "
                                                                f"{moved:+.2f}% vs signal price {ref}"))
    if res.status == "COMPLETE":
        delta = res.fill_price - ref  # keep the planned risk: move stop and target with the actual fill
        if trade.stop_loss:
            trade.stop_loss = round(trade.stop_loss + delta, 2)
        if trade.target:
            trade.target = round(trade.target + delta, 2)
        _opened(db, order, trade, res, now)
        out["filled"] += 1
    elif res.status == "PLACED":  # live order working at the broker: reconciled on the next runs
        _record_order(db, order, res)
        db.commit()
        out["still_open"] += 1
    else:
        _closed_unfilled(db, order, trade, OrderStatus.REJECTED, f"broker rejected the queued order: {res.message}")
        out["cancelled"] += 1


def process_pending(db: Session, md=None, now: datetime | None = None, kite_fn=None, broker_factory=None) -> dict:
    now = now or utcnow()
    holidays = settings_store.get(db, "holidays")
    cfg = settings_store.get(db, "amo")
    open_now = calendar.is_market_open(now, holidays)
    past_cutoff = open_now and not calendar.before(now, cfg["cancel_unfilled_after"])
    halted = mode_service.get_state(db).halted
    out = {"filled": 0, "cancelled": 0, "still_open": 0}
    kite = None
    for order in _pending_entries(db):
        trade = db.get(Trade, order.trade_id) if order.trade_id else None
        if trade is None or trade.status != TradeStatus.PENDING:
            continue
        if order.variety == "queued":  # Syena's own after-hours queue (paper and live)
            if halted:
                _closed_unfilled(db, order, trade, OrderStatus.CANCELLED, "cancelled: trading halted")
                out["cancelled"] += 1
            elif not open_now or calendar.before(now, cfg.get("place_after", "09:16")):
                out["still_open"] += 1
            else:
                if broker_factory is None:
                    from syena.execution.engine import default_broker_factory

                    broker_factory = default_broker_factory(md)
                _place_queued(db, order, trade, md, now, cfg, past_cutoff, broker_factory, out)
            continue
        if order.mode == "PAPER":
            if halted:
                _closed_unfilled(db, order, trade, OrderStatus.CANCELLED, "cancelled: trading halted")
                out["cancelled"] += 1
                continue
            if order.variety != "amo" or not open_now:
                out["still_open"] += 1
                continue
            quote = None
            if md is not None:
                try:
                    quote = md.ltp([order.symbol]).get(order.symbol)
                except Exception as e:
                    log.warning("amo quote failed", extra={"stock": order.symbol, "error": str(e)})
            if quote:
                req = OrderRequest(order.idempotency_key, order.symbol, order.side, order.qty, order.product, "ENTRY",
                                   quote, "LIMIT", order.price)
                res = PaperBroker().place(db, req)
                if res.status == "COMPLETE":
                    _opened(db, order, trade, res, now)
                    out["filled"] += 1
                    continue
                if "insufficient paper cash" in res.message:
                    _closed_unfilled(db, order, trade, OrderStatus.REJECTED, res.message)
                    out["cancelled"] += 1
                    continue
            if past_cutoff:
                _closed_unfilled(db, order, trade, OrderStatus.CANCELLED,
                                 f"price stayed above the AMO limit {order.price} until {cfg['cancel_unfilled_after']}")
                out["cancelled"] += 1
            else:
                out["still_open"] += 1
            continue

        # LIVE: Zerodha holds the order; read its status and act on it
        if not order.broker_order_id:
            continue
        if kite is None:
            if kite_fn is None:
                from syena.market import kite as kite_session

                kite_fn = lambda: kite_session.client(db)  # noqa: E731
            try:
                kite = kite_fn()
            except Exception as e:
                log.warning("cannot reconcile live orders", extra={"error": str(e)})
                return out
        try:
            hist = kite.order_history(order.broker_order_id)
        except Exception as e:
            log.warning("order history failed", extra={"stock": order.symbol, "error": str(e)})
            continue
        last = hist[-1] if hist else {}
        status = last.get("status", "")
        if status == "COMPLETE":
            _opened(db, order, trade, OrderResult("COMPLETE", float(last.get("average_price") or 0),
                                                  int(last.get("filled_quantity") or order.qty), order.broker_order_id,
                                                  message="filled at Zerodha"), now)
            out["filled"] += 1
        elif status in ("REJECTED", "CANCELLED"):
            _closed_unfilled(db, order, trade, OrderStatus(status), last.get("status_message") or status.lower())
            out["cancelled"] += 1
        elif halted or (order.variety in ("amo", "fromq") and past_cutoff):
            why = "cancelled: trading halted" if halted else f"not filled by {cfg['cancel_unfilled_after']}"
            try:
                _cancel_live(kite, order)
                _closed_unfilled(db, order, trade, OrderStatus.CANCELLED, why)
                out["cancelled"] += 1
            except Exception:
                out["still_open"] += 1
        else:
            out["still_open"] += 1
    return out
