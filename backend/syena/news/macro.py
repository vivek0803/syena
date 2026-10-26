"""Broad news (sanctions, tariffs, crude oil, RBI, rupee, monsoon, budget, bans...) mapped to the listed companies it
helps or hurts. Example: new EU sanctions or tariffs -> exporters with big European sales (textiles, auto parts, IT
services with European clients) under selling pressure.

Steps: a cheap keyword check picks likely macro items (no AI cost for the rest); the triage model names the themes,
the industries and well-known exposed companies with a direction; Syena keeps only real, liquid NSE stocks (named
companies first, then the largest stocks in the named industries) and sends each through the normal news analysis
and trade gate with a note on why it was linked. Nothing trades on the mapping alone.
"""
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.ai import llm
from syena.ai.prompts import untrusted
from syena.models import NewsAnalysis, NewsItem, Stock, StockDaily

log = logging.getLogger("syena.macro")

MACRO_WORDS = re.compile(
    r"\b(sanction|sanctions|tariff|tariffs|duty|duties|anti-dumping|export ban|import ban|ban on|embargo|war|ceasefire|"
    r"crude|brent|oil price|opec|gas price|coal|steel price|metal prices|gold price|rupee|dollar index|forex|"
    r"rbi|repo rate|rate cut|rate hike|inflation|cpi|wpi|gdp|fed|federal reserve|ecb|bond yield|"
    r"monsoon|rainfall|el nino|heatwave|budget|gst|tax cut|subsidy|pli|disinvestment|policy|ministry|cabinet|"
    r"government approves|regulator|sebi circular|trai|dgca|fssai|usfda alert|china|europe|european union|eu|"
    r"united states|us administration|trade deal|free trade|fta|shipping|freight|red sea|semiconductor|chip)\b", re.I)


def looks_macro(item: NewsItem) -> bool:
    """Exchange filings are about one company; other items qualify when they mention a macro theme."""
    src = (item.source or "").upper()
    if src.startswith(("NSE", "BSE")) or "nseindia" in (item.url or "") or "bseindia" in (item.url or ""):
        return False
    return bool(MACRO_WORDS.search(f"{item.title} {(item.body or '')[:600]}"))


def industries(db: Session) -> list[str]:
    rows = db.execute(select(Stock.industry, func.count()).where(Stock.active.is_(True), Stock.industry.is_not(None))
                      .group_by(Stock.industry).order_by(func.count().desc())).all()
    return [r[0] for r in rows][:160]


def _liquid_largest(db: Session, industry_names: list[str], limit: int) -> list[str]:
    """The biggest active stocks (by market cap, at least the trade gate's minimum) in these industries."""
    if not industry_names:
        return []
    g = settings_store.get(db, "trade_gate")
    min_cap = float(g.get("min_market_cap_cr") or 0)
    syms = [s for (s,) in db.execute(select(Stock.symbol).where(Stock.active.is_(True), Stock.industry.in_(industry_names)))]
    caps = {}
    for r in db.scalars(select(StockDaily).where(StockDaily.symbol.in_(syms), StockDaily.market_cap_cr.is_not(None)).order_by(StockDaily.day)):
        caps[r.symbol] = r.market_cap_cr
    ranked = sorted((s for s in syms if caps.get(s, 0) >= min_cap), key=lambda s: -caps.get(s, 0))
    return ranked[:limit]


def map_impact(db: Session, item: NewsItem) -> list[dict]:
    """[{symbol, direction (positive|negative), theme, why, confidence, source: named|industry}] for a macro item.
    Records a NewsAnalysis (stage 'macro') so the News page shows the mapping."""
    from syena.news import mapping
    from syena.news.pipeline import item_text

    cfg = settings_store.get(db, "news")
    limit = int(cfg.get("macro_max_stocks") or 5)
    inds = industries(db)
    res = llm.call(db, "news_macro", "macro_impact", tier="triage", max_tokens=900, variables={
        "industries": ", ".join(inds) or "(industry list not loaded yet)",
        "item": untrusted(item_text(item), "news"),
    })
    d = res.data or {}
    out: list[dict] = []
    if d.get("is_macro"):
        known_inds = set(inds)
        for th in d.get("themes") or []:
            direction = "negative" if str(th.get("direction", "")).lower().startswith("neg") else "positive"
            conf = int(th.get("confidence") or 0) if str(th.get("confidence") or "0").isdigit() else 0
            if conf < int(cfg.get("macro_min_confidence") or 60):
                continue
            names = " ".join(str(c) for c in th.get("companies") or [])
            named = list(mapping.candidates(db, f" {names} ", None, max_n=10)) if names.strip() else []
            ind_list = [i for i in th.get("industries") or [] if i in known_inds]
            for sym, src in [(s, "named") for s in named] + [(s, "industry") for s in _liquid_largest(db, ind_list, 3)]:
                if sym not in {o["symbol"] for o in out}:
                    out.append({"symbol": sym, "direction": direction, "theme": str(th.get("theme") or "")[:120],
                                "why": str(th.get("why") or "")[:300], "confidence": conf, "source": src})
    out = sorted(out, key=lambda o: (o["source"] != "named", -o["confidence"]))[:limit]
    db.add(NewsAnalysis(news_item_id=item.id, stage="macro", relevant=bool(out), symbols=[o["symbol"] for o in out],
                        event_type="macro", summary=str(d.get("summary") or "")[:500],
                        result={"themes": d.get("themes") or [], "stocks": out, "is_macro": bool(d.get("is_macro"))},
                        model=res.model, prompt_version=res.prompt_version, ai_call_id=res.ai_call_id))
    if out:
        item.status = "MACRO"
    db.commit()
    return out


def note_for(o: dict) -> str:
    return (f"[Macro link made by Syena, not stated in the article: this broad news was linked to the stock through the "
            f"theme '{o['theme']}' ({o['direction']} expected; {o['why']}). Judge whether the company's actual exposure "
            f"is big enough to move the stock; if the link is weak or uncertain, answer HOLD.]")
