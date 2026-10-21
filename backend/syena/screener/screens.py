"""Fundamental screens: plain conditions over company figures + today's ratios, run daily, with change alerts
and optional technical signals on the stocks that pass.

Condition language (case-insensitive):  roe > 15 and debt to equity < 0.5 and (pe < 30 or pb < 3) and market cap > 1000
Units: ratios/percent fields in %, market cap in ₹ crore, debt to equity as a ratio (0.5 = 50%).
"""
import logging
import re
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.db import utcnow
from syena.models import Algo, Screen, ScreenRun, Stock, StockDaily

log = logging.getLogger("syena.screener")

# canonical field -> (table, column, label)
FIELDS = {
    "pe": ("daily", "pe", "P/E"), "pb": ("daily", "pb", "P/B"), "ps": ("daily", "ps", "Price/Sales"),
    "ev_ebitda": ("daily", "ev_ebitda", "EV/EBITDA"), "market_cap": ("daily", "market_cap_cr", "Market cap (₹ cr)"),
    "dividend_yield": ("daily", "dividend_yield", "Dividend yield %"), "earnings_yield": ("daily", "earnings_yield", "Earnings yield %"),
    "price": ("daily", "close", "Price"), "volume": ("daily", "volume", "Volume"), "change": ("daily", "change_pct", "Day change %"),
    "pct_from_52w_high": ("daily", "pct_from_52w_high", "% from 52-week high"),
    "high_52w": ("daily", "w52_high", "52-week high"), "low_52w": ("daily", "w52_low", "52-week low"),
    "roe": ("stock", "roe", "ROE %"), "roa": ("stock", "roa", "ROA %"), "debt_to_equity": ("stock", "debt_to_equity", "Debt/Equity"),
    "current_ratio": ("stock", "current_ratio", "Current ratio"), "sales_growth": ("stock", "sales_growth", "Sales growth % (YoY)"),
    "profit_growth": ("stock", "profit_growth", "Profit growth % (YoY)"), "opm": ("stock", "operating_margin", "Operating margin %"),
    "npm": ("stock", "net_margin", "Net margin %"), "eps": ("stock", "eps_ttm", "EPS (TTM)"),
    "book_value": ("stock", "book_value_ps", "Book value / share"), "promoter_holding": ("stock", "insider_holding", "Promoter/insider holding %"),
    "dividend_ps": ("stock", "dividend_ps", "Dividend / share"),
}
ALIASES = [  # longest first
    ("percent from 52 week high", "pct_from_52w_high"), ("% from 52 week high", "pct_from_52w_high"),
    ("from 52 week high", "pct_from_52w_high"), ("from 52w high", "pct_from_52w_high"),
    ("price to earnings", "pe"), ("price to book", "pb"), ("price to sales", "ps"), ("return on equity", "roe"),
    ("return on assets", "roa"), ("debt to equity", "debt_to_equity"), ("debt/equity", "debt_to_equity"),
    ("operating profit margin", "opm"), ("operating margin", "opm"), ("net profit margin", "npm"), ("net margin", "npm"),
    ("profit margin", "npm"), ("sales growth", "sales_growth"), ("revenue growth", "sales_growth"),
    ("profit growth", "profit_growth"), ("earnings growth", "profit_growth"), ("dividend yield", "dividend_yield"),
    ("earnings yield", "earnings_yield"), ("market capitalization", "market_cap"), ("market cap", "market_cap"),
    ("promoter holding", "promoter_holding"), ("insider holding", "promoter_holding"), ("current ratio", "current_ratio"),
    ("book value", "book_value"), ("52 week high", "high_52w"), ("52 week low", "low_52w"), ("ev/ebitda", "ev_ebitda"),
    ("pe ratio", "pe"), ("p/e", "pe"), ("p/b", "pb"), ("p/s", "ps"), ("d/e", "debt_to_equity"), ("mcap", "market_cap"),
    ("current price", "price"), ("close", "price"), ("cmp", "price"), ("day change", "change"),
]
TOKEN = re.compile(r"\s*(>=|<=|!=|>|<|=|\(|\)|-?\d+(?:\.\d+)?|[a-z_0-9]+)")


class ScreenError(ValueError):
    pass


def _normalise(text: str) -> str:
    t = " " + (text or "").lower().replace("₹", " ").replace(",", "") + " "
    for a, c in ALIASES:
        t = re.sub(r"(?<![a-z_])" + re.escape(a) + r"(?![a-z_])", f" {c} ", t)
    t = re.sub(r"(\d)\s*(%|cr|crores?)\b", r"\1", t)
    t = t.replace("%", " ")
    t = re.sub(r"\bis\b|\bthan\b", " ", t)
    for a, b in (("greater or equal", ">="), ("less or equal", "<="), ("at least", ">="), ("at most", "<="),
                 ("above", ">"), ("over", ">"), ("greater", ">"), ("more", ">"), ("below", "<"), ("under", "<"), ("less", "<")):
        t = re.sub(r"\b" + a + r"\b", f" {b} ", t)
    return t


def parse_conditions(text: str) -> dict:
    """-> AST {"op":"and"|"or"|"not"|"cmp", ...}. Raises ScreenError with a helpful message."""
    t = _normalise(text)
    toks, pos = [], 0
    while pos < len(t):
        if t[pos].isspace():
            pos += 1
            continue
        m = TOKEN.match(t, pos)
        if not m:
            raise ScreenError(f"can't read '{t[pos:pos + 15].strip()}'")
        toks.append(m.group(1))
        pos = m.end()
    if not toks:
        raise ScreenError("enter at least one condition, e.g. 'roe > 15 and pe < 30'")
    i = [0]

    def peek():
        return toks[i[0]] if i[0] < len(toks) else None

    def take():
        i[0] += 1
        return toks[i[0] - 1] if i[0] - 1 < len(toks) else None

    def expr():
        args = [term()]
        while peek() == "or":
            take()
            args.append(term())
        return args[0] if len(args) == 1 else {"op": "or", "args": args}

    def term():
        args = [factor()]
        while peek() == "and":
            take()
            args.append(factor())
        return args[0] if len(args) == 1 else {"op": "and", "args": args}

    def factor():
        tok = peek()
        if tok is None:
            raise ScreenError("condition ends unexpectedly")
        if tok == "not":
            take()
            return {"op": "not", "arg": factor()}
        if tok == "(":
            take()
            node = expr()
            if take() != ")":
                raise ScreenError("unbalanced brackets")
            return node
        field = take()
        if field not in FIELDS:
            raise ScreenError(f"unknown field '{field}'. Fields: " + ", ".join(sorted(FIELDS)))
        cmp = take() if peek() in (">", ">=", "<", "<=", "=", "!=") else None
        if cmp is None:
            raise ScreenError(f"'{field}' needs a comparison, e.g. '{field} > 10'")
        val = take() if peek() is not None else None
        if val is None or not re.fullmatch(r"-?\d+(?:\.\d+)?", val):
            if val in FIELDS:  # field vs field, e.g. price > high_52w
                return {"op": "cmp", "field": field, "cmp": cmp, "other": val}
            raise ScreenError(f"'{field} {cmp}' needs a number")
        return {"op": "cmp", "field": field, "cmp": cmp, "value": float(val)}

    node = expr()
    if i[0] != len(toks):
        raise ScreenError(f"unexpected '{toks[i[0]]}' (join conditions with 'and' / 'or')")
    return node


def fields_used(node: dict) -> set[str]:
    if node["op"] == "cmp":
        return {node["field"]} | ({node["other"]} if node.get("other") else set())
    if node["op"] == "not":
        return fields_used(node["arg"])
    return set().union(*(fields_used(a) for a in node["args"]))


def evaluate(node: dict, row: dict) -> bool:
    op = node["op"]
    if op == "and":
        return all(evaluate(a, row) for a in node["args"])
    if op == "or":
        return any(evaluate(a, row) for a in node["args"])
    if op == "not":
        return not evaluate(node["arg"], row)
    v = row.get(node["field"])
    w = row.get(node["other"]) if node.get("other") else node["value"]
    if v is None or w is None:
        return False  # missing data never passes a screen
    return {">": v > w, ">=": v >= w, "<": v < w, "<=": v <= w, "=": abs(v - w) < 1e-9, "!=": abs(v - w) >= 1e-9}[node["cmp"]]


def describe(node: dict) -> str:
    op = node["op"]
    if op == "cmp":
        rhs = FIELDS[node["other"]][2] if node.get("other") else f"{node['value']:g}"
        return f"{FIELDS[node['field']][2]} {node['cmp']} {rhs}"
    if op == "not":
        return f"NOT {describe(node['arg'])}"
    return "(" + f" {op.upper()} ".join(describe(a) for a in node["args"]) + ")"


# ---------------------------------------------------------------- data rows
def latest_day(db: Session) -> date | None:
    """The most recent day with prices for most stocks. A day where only a handful were refreshed (e.g. from the
    Compare page) doesn't count, so screens never mistake 'not refreshed yet' for 'no data'."""
    days = db.execute(select(StockDaily.day, func.count()).group_by(StockDaily.day)
                      .order_by(StockDaily.day.desc()).limit(10)).all()
    if not days:
        return None
    top = max(c for _, c in days)
    return next(d for d, c in days if c >= top * 0.5)


def rows_for_day(db: Session, day: date | None) -> list[dict]:
    """One merged dict per active stock: company figures + that day's prices/ratios."""
    daily = {r.symbol: r for r in db.scalars(select(StockDaily).where(StockDaily.day == day))} if day else {}
    out = []
    for s in db.scalars(select(Stock).where(Stock.active.is_(True))):
        d = daily.get(s.symbol)
        row = {"symbol": s.symbol, "name": s.name, "sector": s.sector, "industry": s.industry,
               "fundamentals_at": s.fundamentals_at.isoformat() if s.fundamentals_at else None}
        for f, (table, col, _label) in FIELDS.items():
            src = d if table == "daily" else s
            row[f] = getattr(src, col) if src is not None else None
        out.append(row)
    return out


def screen_rows(db: Session, conditions: str, day: date | None = None) -> tuple[dict, list[dict], int]:
    node = parse_conditions(conditions)
    day = day or latest_day(db)
    rows = rows_for_day(db, day)
    used = fields_used(node)
    passing = [r for r in rows if evaluate(node, r)]
    missing = sum(1 for r in rows if any(r.get(f) is None for f in used))
    return node, sorted(passing, key=lambda r: -(r.get("market_cap") or 0)), missing


# ---------------------------------------------------------------- daily run
def technical_signals(db: Session, rule: dict, symbols: list[str], md, day: date) -> list[dict]:
    from syena.algos.rules import describe as describe_node, evaluate as eval_rule

    lookback = int(settings_store.get(db, "screener")["signal_lookback_days"])
    end = utcnow()
    out = []
    for sym in symbols:
        try:
            df = md.candles(sym, "day", end - timedelta(days=lookback), end)
            if len(df) < 5:
                continue
            fired = eval_rule(df, rule["entry"])
            if bool(fired.iloc[-1]):
                out.append({"symbol": sym, "close": round(float(df["close"].iloc[-1]), 2), "detail": describe_node(rule["entry"])})
        except Exception as e:
            log.info("screen signal check failed", extra={"stock": sym, "error": str(e)})
    return out




def linked_screens(db: Session, algo_id: int) -> list[Screen]:
    return list(db.scalars(select(Screen).where(Screen.algo_id == algo_id)))


def run_screen(db: Session, screen: Screen, md=None, day: date | None = None, notify=None) -> ScreenRun:
    day = day or latest_day(db)
    if day is None:
        raise ScreenError("Prices aren't loaded yet: click 'Update prices now' on the Screener page first.")
    node, passing, missing = screen_rows(db, screen.conditions, day)
    members = sorted(r["symbol"] for r in passing)
    prev_run = db.scalar(select(ScreenRun).where(ScreenRun.screen_id == screen.id).order_by(ScreenRun.id.desc()).limit(1))
    first = prev_run is None
    prev = set(prev_run.members if prev_run else [])
    added, removed = sorted(set(members) - prev), sorted(prev - set(members))
    if prev_run is not None:
        # Not real changes: a stock that just got its company figures for the first time, or one whose data is
        # missing today. Only genuine moves in or out of the screen are reported.
        used = fields_used(node)
        by_sym = {r["symbol"]: r for r in rows_for_day(db, day)} if (added or removed) else {}
        def newly_covered(sym):
            st = db.get(Stock, sym)
            return st is not None and st.fundamentals_at is not None and st.fundamentals_at > prev_run.ran_at and sym not in prev
        added = [x for x in added if not newly_covered(x)]
        removed = [x for x in removed if x in by_sym and all(by_sym[x].get(f) is not None for f in used)]
    signals = technical_signals(db, screen.signal_rule, members, md, day) if (screen.signal_rule and md is not None and members) else []
    run = ScreenRun(screen_id=screen.id, day=day or utcnow().date(), members=members, added=[] if first else added,
                    removed=[] if first else removed, signals=signals, skipped_no_data=missing)
    db.add(run)
    screen.members, screen.last_run_at = members, utcnow()
    if screen.algo_id:  # keep the linked algo trading exactly the screen's stocks
        algo = db.get(Algo, screen.algo_id)
        if algo is not None and algo.status != "DELETED":
            by_cap = {r["symbol"]: r.get("market_cap") or 0 for r in passing}
            algo.symbols = sorted(members, key=lambda x: -by_cap.get(x, 0))  # biggest companies first
    db.commit()
    if screen.alerts and (first or added or removed or signals):
        text = alert_text(screen, run, first)
        try:
            (notify or _telegram)(db, text)
        except Exception as e:
            log.info("screen alert failed", extra={"error": str(e)})
    log.info("screen run", extra={"screen": screen.name, "members": len(members), "added": len(run.added),
                                  "removed": len(run.removed), "signals": len(signals)})
    return run


def alert_text(screen: Screen, run: ScreenRun, first: bool) -> str:
    lines = [f"📋 <b>{screen.name}</b>: {len(run.members)} stocks ({run.day:%d %b})"]
    if first:
        lines.append("First run: " + (", ".join(run.members[:30]) + (" …" if len(run.members) > 30 else "") if run.members else "no stocks pass yet"))
    if run.added:
        lines.append("➕ Added: " + ", ".join(run.added[:30]))
    if run.removed:
        lines.append("➖ Removed: " + ", ".join(run.removed[:30]))
    for s in run.signals[:15]:
        lines.append(f"📈 {s['symbol']} ₹{s['close']}: {s['detail']}")
    return "\n".join(lines)


def _telegram(db: Session, text: str) -> None:
    from syena.notify import telegram

    telegram.notify(db, "screen", text)


def run_all(db: Session, md=None, day: date | None = None) -> dict:
    out = {}
    for sc in db.scalars(select(Screen).order_by(Screen.id)):
        try:
            r = run_screen(db, sc, md, day)
            out[sc.name] = {"members": len(r.members), "added": len(r.added), "removed": len(r.removed), "signals": len(r.signals)}
        except Exception as e:
            db.rollback()
            log.error("screen failed", extra={"screen": sc.name, "error": str(e)})
            out[sc.name] = {"error": str(e)}
    return out
