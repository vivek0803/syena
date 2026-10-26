"""Everything you own, in one place: stocks bought through Manual invest (open trades) and the shares in your
broker / CAS holdings. The news pipeline, alerts and Ask AI use this so news about something you hold reaches you."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.models import Trade, TradeStatus


def my_stocks(db: Session) -> dict[str, dict]:
    """{SYMBOL: {"qty", "avg_price", "value", "sources": ["manual trade", "zerodha holding", ...], "trade_ids": [...]}}"""
    out: dict[str, dict] = {}

    def add(sym, qty, avg, value, source, trade_id=None):
        if not sym:
            return
        sym = sym.strip().upper().removesuffix("-EQ")
        d = out.setdefault(sym, {"qty": 0.0, "avg_price": None, "value": 0.0, "sources": [], "trade_ids": []})
        d["qty"] += float(qty or 0)
        d["value"] += float(value or 0)
        d["avg_price"] = d["avg_price"] or avg
        if source not in d["sources"]:
            d["sources"].append(source)
        if trade_id:
            d["trade_ids"].append(trade_id)

    for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN, Trade.side == "BUY", Trade.source == "manual")):
        add(t.stock, t.qty, t.entry_price, (t.last_price or t.entry_price or 0) * (t.qty or 0), f"manual {t.style.value.lower().replace('_', '-')} ({t.mode.lower()})", t.id)
    try:
        from syena.portfolio.analysis import latest_holdings

        rows, _ = latest_holdings(db)
    except Exception:  # portfolio not set up: manual trades only
        rows = []
    for h in rows:
        if h.asset_class in ("equity", "etf") and h.symbol:
            add(h.symbol, h.qty, h.avg_price, h.value, f"{h.source} holding")
    return out


def describe(d: dict) -> str:
    bits = [f"{d['qty']:g} shares" if d["qty"] else None, f"avg ₹{d['avg_price']:,.2f}" if d.get("avg_price") else None,
            f"worth ₹{d['value']:,.0f}" if d.get("value") else None]
    return ", ".join(b for b in bits if b) + f" ({', '.join(d['sources'])})"
