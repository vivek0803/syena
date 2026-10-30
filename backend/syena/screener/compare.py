"""Compare page: any stocks you pick, every metric side by side, price performance, and an AI pick of the best one.

Company figures and daily ratios come from the screener tables (each stock's own latest price row, so a few
stocks refreshed today never hide the rest). Technicals are computed from daily candles (cached in the DB).
"""
import json
import logging
import math
from datetime import timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.algos import indicators as ind
from syena.db import utcnow
from syena.models import Stock, StockDaily, Watchlist
from syena.screener.screens import FIELDS

log = logging.getLogger("syena.compare")

AI_GROUP = 40  # stocks per AI comparison; bigger lists go in rounds (group winners meet in a final)
AI_FINALISTS = 5  # best of each group that reach the final
LIVE_FETCH = 30  # opening a list downloads missing price history for at most this many stocks; "Update data" does all
TECH_FIELDS = {  # key -> label
    "ret_1m": "1-month return %", "ret_3m": "3-month return %", "ret_6m": "6-month return %", "ret_1y": "1-year return %",
    "rsi14": "RSI 14", "vs_dma50": "% vs 50-day avg", "vs_dma200": "% vs 200-day avg",
    "volatility": "Volatility % (1y, annualised)", "max_drawdown": "Max fall from peak % (1y)",
}


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 2)


def metrics(db: Session, symbols: list[str]) -> list[dict]:
    """One row per symbol, in the order given: company figures + that stock's latest prices/ratios."""
    stocks = {s.symbol: s for s in db.scalars(select(Stock).where(Stock.symbol.in_(symbols)))}
    latest = {}
    for r in db.scalars(select(StockDaily).where(StockDaily.symbol.in_(symbols)).order_by(StockDaily.day)):
        latest[r.symbol] = r  # ordered by day, so the last one wins
    out = []
    for sym in symbols:
        s, d = stocks.get(sym), latest.get(sym)
        row = {"symbol": sym, "name": s.name if s else None, "sector": s.sector if s else None,
               "industry": s.industry if s else None, "known": s is not None,
               "price_day": d.day.isoformat() if d else None,
               "fundamentals_at": s.fundamentals_at.isoformat() if s and s.fundamentals_at else None}
        for f, (table, col, _label) in FIELDS.items():
            if table == "computed":
                continue
            src = d if table == "daily" else s
            row[f] = _num(getattr(src, col)) if src is not None else None
        from syena.screener.screens import computed_fields

        row.update(computed_fields(s))
        out.append(row)
    return out


def technicals_from(df: pd.DataFrame) -> dict:
    c = df["close"].astype(float)
    n = len(c)
    out: dict = {}
    for key, bars in (("ret_1m", 21), ("ret_3m", 63), ("ret_6m", 126), ("ret_1y", 252)):
        out[key] = _num((c.iloc[-1] / c.iloc[-1 - bars] - 1) * 100) if n > bars else None
    out["rsi14"] = _num(ind.rsi(c, 14).iloc[-1]) if n > 15 else None
    for key, w in (("vs_dma50", 50), ("vs_dma200", 200)):
        out[key] = _num((c.iloc[-1] / ind.sma(c, w).iloc[-1] - 1) * 100) if n >= w else None
    last = c.iloc[-252:]
    out["volatility"] = _num(last.pct_change().std() * math.sqrt(252) * 100) if len(last) > 20 else None
    out["max_drawdown"] = _num(((last / last.cummax()) - 1).min() * 100) if len(last) > 1 else None
    step = max(1, len(last) // 120)  # ~120 points for the performance chart
    pts = last.iloc[::step]
    if pts.index[-1] != last.index[-1]:
        pts = pd.concat([pts, last.iloc[-1:]])
    out["series"] = [[ts.date().isoformat(), round(float(v), 2)] for ts, v in pts.items()]
    return out


def technicals(md, symbols: list[str], db: Session | None = None, live_fetch: int | None = None) -> dict[str, dict]:
    """Technicals from daily candles. With `live_fetch`, only that many stocks may download missing history
    (Angel allows ~1.4 a second); the rest use what's already saved, so a big list opens instantly."""
    from syena.market.data import cached_candles

    end = utcnow()
    start = end - timedelta(days=400)
    out = {}
    fetched = 0
    for sym in symbols:
        try:
            df = None
            if live_fetch is not None and db is not None:
                df = cached_candles(db, sym, "day", start, end)
                fresh = len(df) >= 2 and (end - df.index[-1].to_pydatetime()).days <= 4
                if not fresh:
                    if fetched >= live_fetch:
                        out[sym] = technicals_from(df) if len(df) >= 2 else {"error": "not loaded yet: click Update data"}
                        if len(df) >= 2:
                            out[sym]["stale"] = True
                        continue
                    fetched += 1
                    df = None
            if df is None:
                df = md.candles(sym, "day", start, end)
            out[sym] = technicals_from(df) if len(df) >= 2 else {"error": "no price history"}
        except Exception as e:
            log.info("compare technicals failed", extra={"stock": sym, "error": str(e)})
            out[sym] = {"error": str(e)[:160]}
    return out


def missing_data(db: Session, symbols: list[str]) -> dict:
    rows = metrics(db, symbols)
    today = utcnow().date()
    return {"unknown": [r["symbol"] for r in rows if not r["known"]],
            "no_fundamentals": [r["symbol"] for r in rows if r["known"] and not r["fundamentals_at"]],
            "stale_prices": [r["symbol"] for r in rows if r["known"] and (not r["price_day"] or
                             (today - pd.Timestamp(r["price_day"]).date()).days > 4)]}


def refresh(db: Session, md, symbols: list[str]) -> dict:
    """Bring these stocks up to date: today's prices for all of them, company figures for any that lack them."""
    from syena.screener import data

    have = set(db.scalars(select(Stock.symbol).where(Stock.symbol.in_(symbols))))
    new = [s for s in symbols if s not in have]
    if new:  # not in the stock list yet (e.g. universe never built): add the ones the broker knows
        known = set(data.universe_from_md(db, md))
        for s in new:
            if s in known:
                db.add(Stock(symbol=s, active=True))
        db.commit()
    prices = data.refresh_daily(db, md, symbols=symbols)
    need_f = [r["symbol"] for r in metrics(db, symbols) if r["known"] and not r["fundamentals_at"]]
    fund = data.refresh_fundamentals(db, symbols=need_f) if need_f else {"ok": 0}
    if need_f:
        data.recompute_latest(db, need_f)
    tech = technicals(md, symbols)  # downloads/updates price history for every stock (saved for next time)
    return {"prices": prices.get("updated", 0), "fundamentals": fund.get("ok", 0),
            "history": sum(1 for t in tech.values() if "error" not in t)}


# ------------------------------------------------------------------ AI pick
HORIZONS = {"LONG_TERM": "long-term investment (1-5 years): weight business quality, growth, balance sheet and valuation",
            "SWING": "swing trade (days to weeks): weight trend, momentum and technicals, with valuation as a sanity check"}


def _table(db: Session, syms: list[str], tech: dict) -> list[dict]:
    keep = [f for f in FIELDS if f not in ("volume", "high_52w", "low_52w", "book_value", "dividend_ps")]
    table = []
    for r in metrics(db, syms):
        item = {"symbol": r["symbol"], "name": r["name"], "sector": r["sector"]}
        item.update({f: r[f] for f in keep if r.get(f) is not None})
        t = tech.get(r["symbol"]) or {}
        item.update({k: t[k] for k in TECH_FIELDS if t.get(k) is not None})
        item["missing"] = [f for f in ("pe", "roe", "sales_growth", "debt_to_equity", "ret_1y") if item.get(f) is None]
        table.append(item)
    return table


AI_EXPLAIN = 12  # the AI writes about the top stocks; the rest get a sentence from the formula


def _ask(db: Session, table: list[dict], scores: dict, horizon: str, note: str | None) -> tuple[dict, str]:
    from syena.ai import llm
    from syena.ai.prompts import untrusted

    keep = [f for f in FIELDS if f not in ("volume", "high_52w", "low_52w", "book_value", "dividend_ps")]
    top = [dict(t, formula_score=scores[t["symbol"]]["score"], breakdown=scores[t["symbol"]]["breakdown"]) for t in table[:AI_EXPLAIN]]
    others = [f"{t['symbol']}: {scores[t['symbol']]['score']}" for t in table[AI_EXPLAIN:]]
    from syena import settings_store

    res = llm.call(db, "stock_compare", "stock_compare", tier="premium", max_tokens=1500 + 120 * len(top), temperature=0,
                   web_search=int(settings_store.get(db, "ai_web").get("compare") or 0),
                   notes=("compare", f"{horizon} {note or ''} " + " ".join(f"{t['symbol']} {t.get('name') or ''} {t.get('industry') or ''}" for t in top),
                          [t["symbol"] for t in top]),
                   variables={
        "horizon": HORIZONS.get(horizon, HORIZONS["LONG_TERM"]),
        "fields": json.dumps({**{f: FIELDS[f][2] for f in keep}, **TECH_FIELDS}),
        "stocks": json.dumps(top, ensure_ascii=False),
        "others": ", ".join(others) or "(none)",
        "note": untrusted(note.strip()[:500], "user_preference") if note and note.strip() else "(none)",
    })
    data = dict(res.data or {})
    if res.sources:
        data["sources"] = res.sources
    if res.notes:
        data["owner_notes"] = res.notes
    return data, res.model


def ai_compare(db: Session, wl: Watchlist, md=None, horizon: str = "LONG_TERM", note: str | None = None) -> dict:
    """Rank any number of stocks with the fixed formula (screener/scoring.py), then let the AI explain the top ones
    and nudge a score by up to 10 points where the formula can't see something. Same inputs, same answer."""
    from syena.screener import scoring

    syms = list(wl.symbols or [])
    if len(syms) < 2:
        raise ValueError("Add at least two stocks to compare.")
    tech = technicals(md, syms, db, live_fetch=LIVE_FETCH) if md is not None else {}
    table = {t["symbol"]: t for t in _table(db, syms, tech)}
    key = _input_key(table, horizon, note, _notes_version(db))
    prev = wl.ai_result or {}
    if prev.get("input_key") == key and prev.get("top_pick"):
        return {**prev, "reused": True}
    scores = scoring.score(list(table.values()), horizon)
    order = sorted(table, key=lambda s: (-scores[s]["score"], s))
    ai, model = _ask(db, [table[s] for s in order], scores, horizon, note)
    adj = {}
    for a in ai.get("adjustments") or []:
        sym = str(a.get("symbol", "")).upper()
        try:
            pts = max(-10.0, min(10.0, float(a.get("points") or 0)))
        except (TypeError, ValueError):
            continue
        if sym in table and pts:
            adj[sym] = {"points": pts, "reason": str(a.get("reason") or "")[:300]}
    why = {str(k).upper(): str(v)[:400] for k, v in (ai.get("why") or {}).items()} if isinstance(ai.get("why"), dict) else {}
    ranking = []
    for s in order:
        sc = scores[s]
        final = round(max(0.0, min(100.0, sc["score"] + adj.get(s, {}).get("points", 0))), 1)
        ranking.append({"symbol": s, "score": round(final), "formula_score": sc["score"], "adjustment": adj.get(s),
                        "verdict": scoring.verdict(final), "breakdown": sc["breakdown"], "coverage": sc["coverage"],
                        "why": why.get(s) or scoring.explain(s, sc)})
    ranking.sort(key=lambda r: (-r["score"], -r["formula_score"], r["symbol"]))
    lst = lambda k: [str(x)[:300] for x in (ai.get(k) or [])][:6]  # noqa: E731
    out = {"top_pick": ranking[0]["symbol"], "ranking": ranking, "top_pick_reasons": lst("top_pick_reasons"),
           "risks": lst("risks"), "data_gaps": lst("data_gaps"), "summary": str(ai.get("summary") or "")[:800],
           "sources": [x for x in (ai.get("sources") or []) if isinstance(x, dict)][:10],
           "owner_notes": ai.get("owner_notes") or [],
           "method": "fixed formula + AI review (max ±10 points)", "rubric": {k: w for k, (w, _) in scoring.RUBRICS[horizon if horizon in scoring.RUBRICS else "LONG_TERM"].items()},
           "horizon": horizon, "model": model, "stocks": list(wl.symbols or []), "note": note or None, "rounds": 1,
           "input_key": key}
    wl.ai_result, wl.ai_at = out, utcnow()
    db.commit()
    return out


def _notes_version(db: Session) -> str:
    """Changes when the owner adds, edits or pauses a note, so Compare asks the AI again."""
    from sqlalchemy import func

    from syena.models import OwnerNote

    n, last = db.execute(select(func.count(), func.max(OwnerNote.updated_at)).where(OwnerNote.active.is_(True))).one()
    return f"{n}:{last}" if n else ""


def _input_key(table: dict, horizon: str, note: str | None, notes_version: str = "") -> str:
    import hashlib

    rows = [{k: v for k, v in table[s].items() if k != "missing"} for s in sorted(table)]
    blob = json.dumps({"rows": rows, "horizon": horizon, "note": (note or "").strip().lower(), "prompt": "stock_compare/v3",
                       **({"owner_notes": notes_version} if notes_version else {})},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _clean(d: dict, syms: list[str]) -> dict:
    """Keep the AI's answer to the stocks actually given (no invented tickers)."""
    allowed = set(syms)
    ranking = []
    for r in d.get("ranking") or []:
        sym = str(r.get("symbol", "")).upper()
        if sym in allowed and sym not in {x["symbol"] for x in ranking}:
            score = r.get("score")
            ranking.append({"symbol": sym, "score": max(0, min(100, int(score))) if isinstance(score, (int, float)) else None,
                            "verdict": r.get("verdict") if r.get("verdict") in ("strong", "good", "neutral", "weak") else "neutral",
                            "why": str(r.get("why") or "")[:400]})
    top = str(d.get("top_pick") or "").upper()
    if top not in allowed:
        top = ranking[0]["symbol"] if ranking else None
    lst = lambda k: [str(x)[:300] for x in (d.get(k) or [])][:6]  # noqa: E731
    return {"top_pick": top, "ranking": ranking, "top_pick_reasons": lst("top_pick_reasons"), "risks": lst("risks"),
            "data_gaps": lst("data_gaps"), "summary": str(d.get("summary") or "")[:800]}
