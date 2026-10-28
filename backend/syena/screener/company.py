"""Company pages, laid out like screener.in: summary ratios, pros and cons, peers, quarterly results, profit & loss,
compounded growth, balance sheet, cash flows, ratios, shareholding and recent announcements.

Statements come from Yahoo Finance's reported figures (annual: the last 4 years; quarterly: the last 4-6 quarters),
stored per period in ₹ crore when the nightly fundamentals job visits a stock. Screener.in shows 10-12 years from
its own database; Yahoo's free data is shorter, and that is said on the page.
"""
import math
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.models import Financials, NewsItem, NewsAnalysis, Stock, StockDaily

CR = 1e7
# our line item -> Yahoo row names (first found wins)
PL_ITEMS = {
    "sales": ["Total Revenue", "Operating Revenue"],
    "expenses": ["Total Expenses", "Operating Expense"],
    "operating_profit": ["EBITDA", "Normalized EBITDA"],
    "other_income": ["Other Income Expense", "Other Non Operating Income Expenses", "Total Other Finance Cost"],
    "interest": ["Interest Expense", "Interest Expense Non Operating"],
    "depreciation": ["Reconciled Depreciation", "Depreciation And Amortization In Income Statement", "Depreciation Amortization Depletion"],
    "pbt": ["Pretax Income"],
    "tax": ["Tax Provision"],
    "net_profit": ["Net Income Common Stockholders", "Net Income", "Net Income Continuous Operations"],
    "eps": ["Diluted EPS", "Basic EPS"],
}
BS_ITEMS = {
    "equity_capital": ["Common Stock", "Capital Stock"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
    "borrowings": ["Total Debt"],
    "total_liabilities": ["Total Liabilities Net Minority Interest"],
    "current_liabilities": ["Current Liabilities"],
    "total_assets": ["Total Assets"],
    "fixed_assets": ["Net PPE"],
    "cwip": ["Construction In Progress"],
    "investments": ["Investments And Advances", "Long Term Equity Investment", "Other Short Term Investments"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "receivables": ["Accounts Receivable", "Receivables"],
    "inventory": ["Inventory"],
}
CF_ITEMS = {
    "operating": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "investing": ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"],
    "financing": ["Financing Cash Flow", "Cash Flow From Continuing Financing Activities"],
    "capex": ["Capital Expenditure"],
    "free_cash_flow": ["Free Cash Flow"],
}
NOT_CRORE = {"eps"}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _periods(df: pd.DataFrame, items: dict) -> dict[date, dict]:
    out: dict[date, dict] = {}
    if df is None or getattr(df, "empty", True):
        return out
    for col in df.columns:
        d = pd.Timestamp(col).date()
        row = {}
        for key, names in items.items():
            for n in names:
                if n in df.index:
                    v = _f(df.loc[n, col])
                    if v is not None:
                        row[key] = round(v if key in NOT_CRORE else v / CR, 2)
                        break
        if row:
            out[d] = row
    return out


def _derive_pl(r: dict) -> dict:
    s = r.get("sales")
    if r.get("operating_profit") is None and s is not None and r.get("expenses") is not None:
        r["operating_profit"] = round(s - r["expenses"], 2)
    if r.get("expenses") is None and s is not None and r.get("operating_profit") is not None:
        r["expenses"] = round(s - r["operating_profit"], 2)
    if s and r.get("operating_profit") is not None:
        r["opm"] = round(r["operating_profit"] / s * 100, 1)
    if r.get("pbt") and r.get("tax") is not None:
        r["tax_pct"] = round(r["tax"] / r["pbt"] * 100, 1)
    return r


def statements(t) -> dict:
    """{"Q": {date: row}, "A": {...}, "BS": {...}, "CF": {...}} from a yfinance Ticker."""
    q = {d: _derive_pl(r) for d, r in _periods(getattr(t, "quarterly_income_stmt", None), PL_ITEMS).items()}
    a = {d: _derive_pl(r) for d, r in _periods(t.income_stmt, PL_ITEMS).items()}
    bs = _periods(t.balance_sheet, BS_ITEMS)
    for r in bs.values():
        if r.get("equity") is not None and r.get("equity_capital") is not None:
            r["reserves"] = round(r["equity"] - r["equity_capital"], 2)
    cf = _periods(t.cashflow, CF_ITEMS)
    for r in cf.values():
        if all(r.get(k) is not None for k in ("operating", "investing", "financing")):
            r["net"] = round(r["operating"] + r["investing"] + r["financing"], 2)
    return {"Q": q, "A": a, "BS": bs, "CF": cf}


def store_statements(db: Session, symbol: str, st: dict, source: str = "yahoo") -> int:
    n = 0
    have = {(f.kind, f.period_end): f for f in db.scalars(select(Financials).where(Financials.symbol == symbol))}
    for kind, rows in (st or {}).items():
        for d, row in rows.items():
            d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
            f = have.get((kind, d))
            if f is None:
                db.add(Financials(symbol=symbol, kind=kind, period_end=d, data=row, source=source, updated_at=utcnow()))
            else:
                f.data, f.source, f.updated_at = {**(f.data or {}), **row}, source, utcnow()
            n += 1
    return n


# ---------------------------------------------------------------- the page
def _table(rows: list[Financials], items: list[tuple[str, str]], fmt_period) -> dict:
    rows = sorted(rows, key=lambda f: f.period_end)
    return {"periods": [fmt_period(f.period_end) for f in rows],
            "rows": [{"key": k, "label": lbl, "values": [(f.data or {}).get(k) for f in rows]} for k, lbl in items
                     if any((f.data or {}).get(k) is not None for f in rows)]}


def _cagr(first, last, years):
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 1)


def pros_cons(s: Stock, d: StockDaily | None, annual: list[Financials], bs: list[Financials]) -> tuple[list[str], list[str]]:
    """Plain rules, the way screener.in phrases its auto pros/cons."""
    pros, cons = [], []
    if s.debt_to_equity is not None and s.debt_to_equity < 0.1:
        pros.append("Company is almost debt free.")
    elif s.debt_to_equity is not None and s.debt_to_equity > 1.5:
        cons.append(f"Debt is high: debt to equity {s.debt_to_equity:.2f}.")
    if s.roe_3y_avg is not None and s.roe_3y_avg >= 18:
        pros.append(f"Company has a good return on equity (ROE) track record: 3 years ROE {s.roe_3y_avg:.1f}%.")
    elif s.roe_3y_avg is not None and s.roe_3y_avg < 10:
        cons.append(f"Company has a low return on equity of {s.roe_3y_avg:.1f}% over the last 3 years.")
    if s.profit_cagr_3y is not None and s.profit_cagr_3y >= 20:
        pros.append(f"Company has delivered good profit growth of {s.profit_cagr_3y:.1f}% CAGR over the last 3 years.")
    if s.sales_cagr_3y is not None and s.sales_cagr_3y < 8:
        cons.append(f"The company has delivered a poor sales growth of {s.sales_cagr_3y:.1f}% over the past three years.")
    if s.dividend_ps and d and d.dividend_yield and d.dividend_yield >= 2:
        pros.append(f"Company is maintaining a healthy dividend payout: yield {d.dividend_yield:.1f}%.")
    if d and d.pb and d.pb > 8:
        cons.append(f"Stock is trading at {d.pb:.1f} times its book value.")
    if s.promoter_holding is not None and s.promoter_holding < 30 and s.promoter_holding > 0:
        cons.append(f"Promoter holding is low: {s.promoter_holding:.1f}%.")
    if s.promoter_change is not None and s.promoter_change <= -2:
        cons.append(f"Promoter holding has decreased by {abs(s.promoter_change):.1f} points over the last quarter.")
    if s.fii_change is not None and s.fii_change >= 1:
        pros.append(f"FIIs increased their stake by {s.fii_change:.1f} points last quarter.")
    if s.free_cash_flow is not None and s.free_cash_flow < 0:
        cons.append("Free cash flow is negative.")
    if bs:
        last = sorted(bs, key=lambda f: f.period_end)
        b0, b1 = (last[0].data or {}).get("borrowings"), (last[-1].data or {}).get("borrowings")
        if b0 and b1 is not None and b1 < b0 * 0.7:
            pros.append("Company has reduced debt.")
    if s.eps_ttm is not None and s.eps_ttm <= 0:
        cons.append("Company is making losses (no P/E).")
    return pros[:6], cons[:6]


def page(db: Session, symbol: str, md=None) -> dict:
    from syena.screener import shareholding
    from syena.screener.compare import metrics

    sym = symbol.strip().upper()
    s = db.get(Stock, sym)
    if s is None:
        raise ValueError(f"{sym} is not in the stock list")
    d = db.scalar(select(StockDaily).where(StockDaily.symbol == sym).order_by(StockDaily.day.desc()).limit(1))
    m = metrics(db, [sym])[0]
    fin = list(db.scalars(select(Financials).where(Financials.symbol == sym)))
    by = {k: [f for f in fin if f.kind == k] for k in ("Q", "A", "BS", "CF")}
    q_fmt = lambda x: x.strftime("%b %Y")  # noqa: E731
    a_fmt = lambda x: x.strftime("Mar %Y") if x.month == 3 else x.strftime("%b %Y")  # noqa: E731
    pl_items = [("sales", "Sales"), ("expenses", "Expenses"), ("operating_profit", "Operating Profit"), ("opm", "OPM %"),
                ("other_income", "Other Income"), ("interest", "Interest"), ("depreciation", "Depreciation"),
                ("pbt", "Profit before tax"), ("tax_pct", "Tax %"), ("net_profit", "Net Profit"), ("eps", "EPS in Rs")]
    annual = sorted(by["A"], key=lambda f: f.period_end)
    # compounded growth like screener.in's boxes (years available on the free feed: up to 3)
    growth = {}
    if len(annual) >= 2:
        yrs = len(annual) - 1
        a0, a1 = annual[0].data or {}, annual[-1].data or {}
        growth = {"years": yrs, "sales": _cagr(a0.get("sales"), a1.get("sales"), yrs), "profit": _cagr(a0.get("net_profit"), a1.get("net_profit"), yrs)}
    if md is not None:
        try:
            df = md.candles(sym, "day", utcnow() - timedelta(days=5 * 366), utcnow())
            c = df["close"].astype(float)
            for label, days in (("1y", 252), ("3y", 756), ("5y", 1260)):
                if len(c) > days:
                    growth[f"price_{label}"] = _cagr(float(c.iloc[-days - 1]), float(c.iloc[-1]), days / 252)
        except Exception:
            pass
    ratios = []
    bs_map = {f.period_end: f.data or {} for f in by["BS"]}
    for f in annual:
        a, b = f.data or {}, bs_map.get(f.period_end, {})
        ebit = (a.get("pbt") or 0) + (a.get("interest") or 0) if a.get("pbt") is not None else None
        cap = (b.get("total_assets") or 0) - (b.get("current_liabilities") or 0) if b.get("total_assets") else None
        row = {"period": a_fmt(f.period_end),
               "roce": round(ebit / cap * 100, 1) if ebit is not None and cap else None,
               "roe": round(a["net_profit"] / b["equity"] * 100, 1) if a.get("net_profit") is not None and b.get("equity") else None,
               "opm": a.get("opm"),
               "debtor_days": round(b["receivables"] / a["sales"] * 365) if b.get("receivables") and a.get("sales") else None,
               "inventory_days": round(b["inventory"] / a["sales"] * 365) if b.get("inventory") and a.get("sales") else None}
        ratios.append(row)
    peers = []
    if s.industry:
        same = [x for (x,) in db.execute(select(Stock.symbol).where(Stock.industry == s.industry, Stock.active.is_(True)))]
        rows = sorted(metrics(db, same), key=lambda r: -(r.get("market_cap") or 0))[:10]
        peers = [{k: r.get(k) for k in ("symbol", "name", "price", "pe", "market_cap", "dividend_yield", "roe", "roce_3y",
                                          "sales_growth", "profit_growth", "peg", "loss_making")} for r in rows]
    pros, cons = pros_cons(s, d, annual, by["BS"])
    news = []
    for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.stage.in_(("triage", "analysis"))).order_by(NewsAnalysis.id.desc()).limit(800)):
        if sym in (a.symbols or []) and a.relevant:
            n = db.get(NewsItem, a.news_item_id)
            if n and all(x["id"] != n.id for x in news):
                news.append({"id": n.id, "title": n.title, "url": n.url, "source": n.source, "at": n.published_at.isoformat()})
            if len(news) >= 12:
                break
    summary = {k: m.get(k) for k in ("price", "market_cap", "high_52w", "low_52w", "pe", "peg", "pb", "book_value", "dividend_yield",
                                      "roe", "roce_3y", "eps", "debt_to_equity", "sales_growth", "profit_growth", "promoter_holding",
                                      "fii_holding", "dii_holding", "fcf_yield", "loss_making", "change", "pct_from_52w_high")}
    return {
        "symbol": sym, "name": s.name, "sector": s.sector, "industry": s.industry, "summary": summary, "price_day": m.get("price_day"),
        "pros": pros, "cons": cons, "peers": peers,
        "quarterly": _table(by["Q"], pl_items, q_fmt),
        "profit_loss": _table(by["A"], pl_items, a_fmt),
        "growth": growth,
        "balance_sheet": _table(by["BS"], [("equity_capital", "Equity Capital"), ("reserves", "Reserves"), ("borrowings", "Borrowings"),
                                           ("total_liabilities", "Total Liabilities"), ("fixed_assets", "Fixed Assets"), ("cwip", "CWIP"),
                                           ("investments", "Investments"), ("cash", "Cash"), ("receivables", "Receivables"),
                                           ("inventory", "Inventory"), ("total_assets", "Total Assets")], a_fmt),
        "cash_flow": _table(by["CF"], [("operating", "Cash from Operating Activity"), ("investing", "Cash from Investing Activity"),
                                       ("financing", "Cash from Financing Activity"), ("net", "Net Cash Flow"), ("capex", "Capex"),
                                       ("free_cash_flow", "Free Cash Flow")], a_fmt),
        "ratios": ratios, "shareholding": shareholding.history(db, sym, 12), "news": news,
        "fundamentals_at": s.fundamentals_at.isoformat() if s.fundamentals_at else None,
        "note": "Statements: Yahoo Finance (free; up to 4 years and 4-6 quarters). Shareholding: NSE filings. Figures in ₹ crore.",
    }
