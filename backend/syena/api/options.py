"""Option chain (view and analysis only: Syena doesn't trade options)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from syena.api.common import market_data
from syena.api.deps import authenticated
from syena.db import get_db
from syena.market import options

router = APIRouter(prefix="/api/options", tags=["options"], dependencies=[Depends(authenticated)])


def _ready(db: Session, md) -> None:
    try:
        options.ensure_contracts(db, md)
    except Exception as e:
        raise HTTPException(502, f"Couldn't load the option list from the broker: {str(e)[:200]}")


@router.get("/underlyings")
def list_underlyings(md=Depends(market_data), db: Session = Depends(get_db)):
    _ready(db, md)
    return options.underlyings(db)


@router.get("/chain")
def get_chain(underlying: str = "NIFTY", expiry: str | None = None, strikes: int = 15,
              md=Depends(market_data), db: Session = Depends(get_db)):
    _ready(db, md)
    try:
        exp = date.fromisoformat(expiry) if expiry else None
    except ValueError:
        raise HTTPException(400, "expiry must be YYYY-MM-DD")
    try:
        return options.chain(db, md, underlying, exp, strikes=max(5, min(strikes, 40)))
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------- paper option trades
from pydantic import BaseModel, Field  # noqa: E402


class OrderIn(BaseModel):
    underlying: str
    expiry: date
    strike: float
    option_type: str = Field(pattern="^(CE|PE)$")
    lots: int = Field(1, ge=1, le=20)
    stop_pct: float = Field(30, gt=0, lt=100)
    target_pct: float = Field(60, gt=0, le=1000)


@router.post("/order")
def buy_option(body: OrderIn, md=Depends(market_data), db: Session = Depends(get_db)):
    """Buy a call or put from the chain (PAPER: simulated fill at the ask, F&O charges, exits on stop/target/expiry)."""
    from syena.execution import engine
    from syena.market.options_trade import enter_option

    _ready(db, md)
    d = enter_option(db, md, body.underlying.upper(), "BUY" if body.option_type == "CE" else "SELL", source="manual",
                     lots=body.lots, expiry=body.expiry, strike=body.strike, option_type=body.option_type,
                     stop_pct=body.stop_pct, target_pct=body.target_pct, reasoning={"why": "bought from the option chain"},
                     broker_factory=engine.default_broker_factory(md))
    return {"decision_id": d.id, "type": d.type.value, "summary": d.summary, "trade_id": d.trade_id}


@router.get("/trades")
def option_trades(db: Session = Depends(get_db)):
    """Syena's option trades (all paper) and how they've done, by strategy."""
    from sqlalchemy import select

    from syena.execution.engine import unrealised
    from syena.models import Algo, Trade, TradeStatus

    trades = list(db.scalars(select(Trade).where(Trade.instrument == "OPT").order_by(Trade.id.desc()).limit(500)))
    names = {a.id: a.name for a in db.scalars(select(Algo))}

    def row(t):
        o = t.option or {}
        return {"id": t.id, "symbol": t.stock, "underlying": o.get("underlying"), "type": o.get("type"), "strike": o.get("strike"),
                "expiry": o.get("expiry"), "lots": o.get("lots"), "qty": t.qty, "source": t.source, "strategy": names.get(t.algo_id, t.source),
                "status": t.status.value, "entry": t.entry_price, "last": t.last_price, "exit": t.exit_price_executed,
                "stop": t.stop_loss, "target": t.target, "invested": t.invested_amount, "pnl": t.profit_loss if t.status == TradeStatus.CLOSED else unrealised(t),
                "exit_reason": t.exit_reason, "opened": t.created_at.isoformat(), "closed": t.closed_at.isoformat() if t.closed_at else None,
                "spot_at_entry": o.get("spot_at_entry"), "iv_at_entry": o.get("iv_at_entry"), "decision_id": t.decision_id}

    rows = [row(t) for t in trades if t.status in (TradeStatus.OPEN, TradeStatus.CLOSED, TradeStatus.PENDING)]
    closed = [r for r in rows if r["status"] == "CLOSED" and r["pnl"] is not None]
    by: dict[str, dict] = {}
    for r in closed:
        b = by.setdefault(r["strategy"], {"strategy": r["strategy"], "trades": 0, "wins": 0, "pnl": 0.0, "invested": 0.0})
        b["trades"] += 1
        b["wins"] += r["pnl"] > 0
        b["pnl"] = round(b["pnl"] + r["pnl"], 2)
        b["invested"] = round(b["invested"] + (r["invested"] or 0), 2)
    for b in by.values():
        b["win_rate"] = round(b["wins"] / b["trades"] * 100, 1) if b["trades"] else None
        b["return_pct"] = round(b["pnl"] / b["invested"] * 100, 2) if b["invested"] else None
    wins = [r["pnl"] for r in closed if r["pnl"] > 0]
    losses = [r["pnl"] for r in closed if r["pnl"] <= 0]
    total = {"trades": len(closed), "open": sum(r["status"] == "OPEN" for r in rows), "pnl": round(sum(r["pnl"] for r in closed), 2),
             "unrealised": round(sum(r["pnl"] or 0 for r in rows if r["status"] == "OPEN"), 2),
             "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
             "avg_win": round(sum(wins) / len(wins), 2) if wins else None, "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
             "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None}
    return {"summary": total, "by_strategy": sorted(by.values(), key=lambda b: -b["trades"]), "trades": rows}
