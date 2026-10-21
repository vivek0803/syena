"""One execution interface, two implementations (spec section 2).

PaperBroker simulates fills from live quotes with slippage and full Zerodha charges against
the paper wallet. KiteBroker places real orders and refuses to place an ENTRY order unless the
system mode is LIVE (defence in depth on top of the engine's own routing).
"""
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from syena import settings_store
from syena.db import utcnow
from syena.market.charges import leg_charges
from syena.models import PaperWallet, TradingMode

log = logging.getLogger("syena.broker")


@dataclass
class OrderRequest:
    idempotency_key: str
    symbol: str
    side: str  # BUY | SELL
    qty: int
    product: str  # MIS | CNC
    purpose: str  # ENTRY | EXIT
    ref_price: float  # latest known price
    order_type: str = "LIMIT"
    limit_price: float | None = None
    variety: str = "regular"  # regular | amo (after-market order, executes at the next open)
    entry_price: float | None = None  # exits only: the position's entry price (paper margin for shorts)


@dataclass
class OrderResult:
    status: str  # COMPLETE | PLACED | REJECTED | FAILED
    fill_price: float | None = None
    filled_qty: int = 0
    broker_order_id: str | None = None
    charges: float = 0.0
    message: str = ""
    attempts: int = 1


class Broker(Protocol):
    name: str

    def place(self, db: Session, req: OrderRequest) -> OrderResult: ...


class LiveOrderForbidden(RuntimeError):
    pass


def tick_round(price: float, tick: float = 0.05) -> float:
    return round(round(price / tick) * tick, 2)


def marketable_limit(side: str, ref_price: float, buffer_bps: float) -> float:
    f = 1 + buffer_bps / 10_000 if side == "BUY" else 1 - buffer_bps / 10_000
    return tick_round(ref_price * f)


# ---------------------------------------------------------------- paper
def get_wallet(db: Session) -> PaperWallet:
    w = db.get(PaperWallet, 1)
    if w is None:
        start = float(settings_store.get(db, "paper")["starting_balance"])
        w = PaperWallet(id=1, starting_balance=start, cash=start)
        db.add(w)
        db.commit()
    return w


def reset_wallet(db: Session, balance: float) -> PaperWallet:
    w = get_wallet(db)
    w.starting_balance = w.cash = balance
    w.updated_at = utcnow()
    db.commit()
    return w


class PaperBroker:
    name = "paper"

    def __init__(self, ltp_fn=None):
        # ltp_fn(symbol) -> float | None. When None, the request's ref_price is the quote.
        self.ltp_fn = ltp_fn

    def place(self, db: Session, req: OrderRequest) -> OrderResult:
        if req.variety == "amo":  # queued: filled by fill_amo() once the market opens
            return OrderResult("PLACED", broker_order_id=f"PAPER-AMO-{req.idempotency_key[:12]}",
                               message=f"after-market order queued for next open (limit {req.limit_price})")
        cfg = settings_store.get(db, "paper")
        quote = (self.ltp_fn(req.symbol) if self.ltp_fn else None) or req.ref_price
        if not quote or quote <= 0:
            return OrderResult("REJECTED", message="no live quote available for paper fill")
        slip = cfg["slippage_bps"] / 10_000
        fill = tick_round(quote * (1 + slip) if req.side == "BUY" else quote * (1 - slip))
        if req.order_type == "LIMIT" and req.limit_price is not None:
            if (req.side == "BUY" and fill > req.limit_price) or (req.side == "SELL" and fill < req.limit_price):
                return OrderResult("REJECTED", message=f"limit {req.limit_price} not marketable at {fill}")
        charges = leg_charges(req.side, req.qty, fill, intraday=req.product == "MIS").total
        wallet = get_wallet(db)
        gross = fill * req.qty
        if req.side == "BUY" and req.purpose == "EXIT" and req.entry_price:
            # covering a short: release the margin blocked at entry plus the profit (or minus the loss)
            entry_gross = req.entry_price * req.qty
            wallet.cash += entry_gross + (entry_gross - gross) - charges
        elif req.side == "BUY" or req.purpose == "ENTRY":
            # a buy, or a short sale (100% of the value is blocked as margin, like the broker's intraday margin)
            if wallet.cash < gross + charges:
                return OrderResult("REJECTED", message=f"insufficient paper cash ₹{wallet.cash:,.2f} for ₹{gross + charges:,.2f}"
                                   + (" (short margin)" if req.side == "SELL" else ""))
            wallet.cash -= gross + charges
        else:
            wallet.cash += gross - charges
        wallet.updated_at = utcnow()
        db.flush()
        return OrderResult("COMPLETE", fill, req.qty, f"PAPER-{req.idempotency_key[:12]}", charges,
                           f"simulated fill @ {fill} (quote {quote}, slippage {cfg['slippage_bps']}bps)")


# ---------------------------------------------------------------- live
class KiteBroker:
    name = "kite"

    def __init__(self, kite_client_fn, mode_fn, sleep=time.sleep):
        self.kite_client_fn = kite_client_fn  # () -> KiteConnect
        self.mode_fn = mode_fn  # (db) -> TradingMode
        self.sleep = sleep

    def place(self, db: Session, req: OrderRequest) -> OrderResult:
        if req.purpose == "ENTRY" and self.mode_fn(db) != TradingMode.LIVE:
            raise LiveOrderForbidden("KiteBroker refused an ENTRY order while the system is not in LIVE mode")
        from kiteconnect import exceptions as kex

        cfg = settings_store.get(db, "orders")
        kite = self.kite_client_fn()
        tag = "".join(ch for ch in req.idempotency_key if ch.isalnum())[:20]
        params = dict(
            variety=req.variety, exchange="NSE", tradingsymbol=req.symbol, transaction_type=req.side,
            quantity=req.qty, product=req.product, tag=tag, validity="DAY",
        )
        if req.order_type == "LIMIT":
            params.update(order_type="LIMIT", price=req.limit_price)
        else:  # market orders must carry explicit market protection or Kite rejects them
            params.update(order_type="MARKET", market_protection=cfg["market_protection_pct"])

        attempts, last_err = 0, ""
        for attempt in range(1, cfg["max_retries"] + 1):
            attempts = attempt
            try:
                existing = [o for o in kite.orders() if o.get("tag") == tag]  # idempotency across retries
                order_id = existing[0]["order_id"] if existing else kite.place_order(**params)
                return self._await(kite, order_id, attempts, polls=2 if req.variety == "amo" else 10)
            except (kex.InputException, kex.OrderException, kex.PermissionException, kex.TokenException) as e:
                return OrderResult("REJECTED", message=f"{type(e).__name__}: {e}", attempts=attempts)
            except Exception as e:  # network / exchange glitches: retry with backoff
                last_err = f"{type(e).__name__}: {e}"
                log.warning("kite order attempt failed", extra={"attempt": attempt, "error": last_err, "stock": req.symbol})
                self.sleep(min(8, 2 ** attempt))
        return OrderResult("FAILED", message=f"gave up after {attempts} attempts: {last_err}", attempts=attempts)

    def _await(self, kite, order_id: str, attempts: int, polls: int = 10) -> OrderResult:
        status, last = "PLACED", {}
        for _ in range(polls):
            hist = kite.order_history(order_id)
            last = hist[-1] if hist else {}
            status = last.get("status", "")
            if status in ("COMPLETE", "REJECTED", "CANCELLED"):
                break
            self.sleep(0.5)
        if status == "COMPLETE":
            return OrderResult("COMPLETE", float(last.get("average_price") or 0), int(last.get("filled_quantity") or 0),
                               order_id, message="filled", attempts=attempts)
        if status in ("REJECTED", "CANCELLED"):
            return OrderResult("REJECTED", broker_order_id=order_id, message=last.get("status_message") or status,
                               attempts=attempts)
        return OrderResult("PLACED", broker_order_id=order_id, message=f"open at broker ({status})", attempts=attempts)
