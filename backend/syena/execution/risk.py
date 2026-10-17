"""Risk limits (spec section 13). Evaluated per mode, so paper and live are tracked separately."""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.db import utcnow
from syena.execution.checks import Check
from syena.market.calendar import IST
from syena.models import Trade, TradeStatus


def _ist_day_start(now: datetime) -> datetime:
    n = now.astimezone(IST)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def open_trades(db: Session, mode: str) -> list[Trade]:
    return list(db.scalars(select(Trade).where(Trade.mode == mode, Trade.status == TradeStatus.OPEN)))


def daily_pnl(db: Session, mode: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    start = _ist_day_start(now)
    realised = db.scalar(
        select(func.coalesce(func.sum(Trade.profit_loss), 0.0)).where(
            Trade.mode == mode, Trade.status == TradeStatus.CLOSED, Trade.closed_at >= start)
    ) or 0.0
    unrealised = 0.0
    for t in open_trades(db, mode):
        if t.last_price and t.entry_price:
            sign = 1 if t.side == "BUY" else -1
            unrealised += sign * (t.last_price - t.entry_price) * t.qty - (t.charges or 0)
    return {"realised": round(realised, 2), "unrealised": round(unrealised, 2), "total": round(realised + unrealised, 2)}


def trades_today(db: Session, mode: str, now: datetime | None = None) -> int:
    start = _ist_day_start(now or utcnow())
    return db.scalar(select(func.count()).select_from(Trade).where(
        Trade.mode == mode, Trade.created_at >= start, Trade.status != TradeStatus.FAILED)) or 0


def check_risk(db: Session, mode: str, symbol: str, amount: float, now: datetime | None = None) -> list[Check]:
    lim = settings_store.get(db, "risk")
    opens = open_trades(db, mode)
    pnl = daily_pnl(db, mode, now)
    n_today = trades_today(db, mode, now)
    exposure = sum(t.invested_amount for t in opens if t.stock == symbol)
    return [
        Check("max_open_positions", len(opens) < lim["max_open_positions"],
              f"{len(opens)} open / limit {lim['max_open_positions']}"),
        Check("max_trades_per_day", n_today < lim["max_trades_per_day"],
              f"{n_today} trades today / limit {lim['max_trades_per_day']}"),
        Check("max_daily_loss", pnl["total"] > -lim["max_daily_loss"],
              f"today's P&L ₹{pnl['total']:,.2f} / max loss ₹{lim['max_daily_loss']:,.0f}", pnl),
        Check("per_stock_exposure", exposure + amount <= lim["max_exposure_per_stock"],
              f"{symbol}: existing ₹{exposure:,.0f} + new ₹{amount:,.0f} / cap ₹{lim['max_exposure_per_stock']:,.0f}"),
    ]


def daily_loss_breached(db: Session, mode: str) -> bool:
    lim = settings_store.get(db, "risk")
    return daily_pnl(db, mode)["total"] <= -lim["max_daily_loss"]
