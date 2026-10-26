"""Ask AI: questions about the stock market, answered with Syena's own data.

For each question Syena works out which stocks it is about, gathers what it knows on them (company figures, price
trend and support/resistance zones, the AI's recent news verdicts, Syena's own decisions, whether you own it), adds
your portfolio and the market's mood when relevant, and asks the model. Anything that is not about markets,
investing or trading (or your Syena data) is politely declined without an answer.
"""
import json
import logging
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.ai import llm
from syena.ai.prompts import untrusted
from syena.db import utcnow
from syena.models import Decision, NewsAnalysis, NewsItem, Stock

log = logging.getLogger("syena.ask")

MAX_SYMBOLS = 4
REFUSAL = ("I can only help with the stock market: stocks, indices, mutual funds and ETFs, trading and investing, "
           "company news and results, and your own Syena data. Try asking about a stock or your portfolio.")
PORTFOLIO_WORDS = re.compile(r"\b(my|portfolio|holding|holdings|own|invested|positions?)\b", re.I)


def find_symbols(db: Session, text: str) -> list[str]:
    from syena.news import mapping

    syms: list[str] = []
    upper = set(re.findall(r"\b[A-Z][A-Z0-9&-]{1,14}\b", text))  # tickers typed in capitals, e.g. TCS, M&M
    if upper:
        syms += [s for s in db.scalars(select(Stock.symbol).where(Stock.symbol.in_(upper)))]
    try:
        for s in mapping.candidates(db, text, None, max_n=8):
            if s not in syms:
                syms.append(s)
    except Exception as e:  # stock list not loaded yet
        log.info("symbol lookup failed", extra={"error": str(e)})
    return syms[:MAX_SYMBOLS]


def _stock_context(db: Session, sym: str, md, mine: dict) -> str:
    from syena.algos import levels
    from syena.news.pipeline import company_facts

    parts = [f"### {sym}"]
    daily = None
    if md is not None:
        try:
            now = utcnow()
            daily = md.candles(sym, "day", now - timedelta(days=400), now)
        except Exception as e:
            parts.append(f"(price history unavailable: {str(e)[:80]})")
    try:
        parts.append(company_facts(db, sym, daily))
    except Exception:
        pass
    if daily is not None and len(daily) > 30:
        last = daily.iloc[-1]
        parts.append(f"Last daily candle {daily.index[-1].date()}: close ₹{float(last['close']):,.2f}")
        try:
            lv = levels.analyse(daily)
            if lv.get("reading"):
                parts.append("Chart: " + lv["reading"])
        except Exception:
            pass
    verdicts = []
    for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.stage == "analysis").order_by(NewsAnalysis.id.desc()).limit(600)):
        if sym in (a.symbols or []):
            r, n = a.result or {}, db.get(NewsItem, a.news_item_id)
            verdicts.append(f"- {a.created_at.date()} {r.get('action')} {r.get('confidence')}: {a.summary or ''}"
                            + (f" [news: {n.title[:140]}]" if n else ""))
            if len(verdicts) >= 4:
                break
    if verdicts:
        parts.append("Syena's recent AI news verdicts:\n" + "\n".join(verdicts))
    decs = [f"- {d.ts.date()} {d.type.value} {d.side or ''}: {d.summary[:160]}" for d in
            db.scalars(select(Decision).where(Decision.symbol == sym).order_by(Decision.id.desc()).limit(3))]
    if decs:
        parts.append("Syena's recent decisions:\n" + "\n".join(decs))
    if sym in mine:
        from syena.portfolio.mine import describe

        parts.append("You own it: " + describe(mine[sym]))
    return "\n".join(parts)


def _portfolio_context(mine: dict) -> str:
    from syena.portfolio.mine import describe

    if not mine:
        return "No holdings recorded in Syena (no open manual-invest trades, no broker/CAS holdings synced)."
    rows = sorted(mine.items(), key=lambda kv: -(kv[1].get("value") or 0))[:25]
    return "Your holdings (largest first):\n" + "\n".join(f"- {s}: {describe(d)}" for s, d in rows)


def _market_context(md) -> str:
    if md is None:
        return "unavailable"
    try:
        snap = md.index_snapshot() or {}
        return "; ".join(f"{k} {v.get('last')} ({v.get('change_pct', 0):+.2f}% today)" for k, v in snap.items() if v) or "unavailable"
    except Exception:
        return "unavailable"


def answer(db: Session, question: str, history: list[dict] | None = None, md=None) -> dict:
    from syena.portfolio.mine import my_stocks

    q = (question or "").strip()[:1000]
    if not q:
        raise ValueError("Type a question.")
    hist = [h for h in (history or []) if h.get("role") in ("user", "assistant") and h.get("content")][-6:]
    # stocks named in this question, or (for follow-ups like "and its debt?") in the previous questions
    syms = find_symbols(db, q) or find_symbols(db, " ".join(h["content"] for h in hist if h["role"] == "user")[-1500:])
    mine = my_stocks(db)
    want_portfolio = bool(PORTFOLIO_WORDS.search(q)) or not syms
    context = [f"Today: {(utcnow() + timedelta(hours=5, minutes=30)).date()} (IST)", f"Market now: {_market_context(md)}"]
    context += [_stock_context(db, s, md, mine) for s in syms]
    if want_portfolio:
        context.append(_portfolio_context(mine))
    convo = "\n".join(f"{h['role']}: {str(h['content'])[:1500]}" for h in hist) or "(none)"
    res = llm.call(db, "ask_ai", "ask_ai", tier="premium", max_tokens=1400, temperature=0.2, variables={
        "question": untrusted(q, "user_question"),
        "history": untrusted(convo, "conversation"),
        "context": untrusted("\n\n".join(context), "syena_data"),
    })
    d = res.data or {}
    in_scope = bool(d.get("in_scope", True))
    text = str(d.get("answer") or "").strip()
    if not in_scope or not text:
        return {"answer": REFUSAL, "in_scope": False, "symbols": [], "model": res.model}
    return {"answer": text[:6000], "in_scope": True, "symbols": syms, "used_portfolio": want_portfolio, "model": res.model,
            "cost_usd": res.cost_usd}
