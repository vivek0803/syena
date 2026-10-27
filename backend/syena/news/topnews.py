"""Top 10 news of the day: built at 6 PM from everything Syena read since 6 PM the day before.

All the day's items go in (company news Syena analysed, broad market news it mapped to stocks, and stories it never
traded because they arrived late, were too small or were judged not tradeable), minus routine exchange paperwork and
copies of the same story. The AI (cheap tier) ranks them by likely impact on NSE prices tomorrow and writes a plain
headline and two lines on why each matters. The list is kept for 30 days and sent on Telegram.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.ai import llm
from syena.ai.prompts import untrusted
from syena.db import utcnow
from syena.market import calendar as cal
from syena.models import AppSetting, NewsAnalysis, NewsItem, StockDaily
from syena.news import sources

log = logging.getLogger("syena.topnews")

STATE_KEY = "_top_news"
KEEP_DAYS = 30
MAX_CANDIDATES = 160
PRIORITY = {"ANALYSED": 0, "MACRO": 1, "TRIAGED": 1, "FILTERED": 2, "STALE": 3, "NEW": 3, "IRRELEVANT": 4, "ERROR": 4, "FAILED": 4}


def _candidates(db: Session, start: datetime, end: datetime) -> list[tuple[NewsItem, dict]]:
    items = [i for i in db.scalars(select(NewsItem).where(NewsItem.published_at >= start, NewsItem.published_at <= end))
             if i.status != "DUPLICATE" and not sources.is_routine(i.title, i.body or "")]
    stages: dict[int, list[NewsAnalysis]] = {}
    if items:
        ids = [i.id for i in items]
        for k in range(0, len(ids), 500):
            for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.news_item_id.in_(ids[k:k + 500]))):
                stages.setdefault(a.news_item_id, []).append(a)
    out = []
    for it in items:
        info: dict = {"symbols": [], "verdict": None, "macro": None}
        for a in stages.get(it.id, []):
            if (a.result or {}).get("routine"):
                info["routine"] = True
            if a.symbols:
                info["symbols"] = list(dict.fromkeys(info["symbols"] + list(a.symbols)))
            if a.stage == "analysis":
                r = a.result or {}
                info["verdict"] = f"{r.get('action')} {r.get('confidence')}"
                info["summary"] = a.summary
            if a.stage == "macro" and (a.result or {}).get("themes"):
                info["macro"] = "; ".join(str(t.get("theme")) for t in (a.result or {}).get("themes", [])[:2])
        if info.get("routine"):
            continue
        out.append((it, info))
    # what reached the AI first, then the rest, newest first within each group; Telegram tips last
    out.sort(key=lambda x: (PRIORITY.get(x[0].status, 4), (x[0].source or "").startswith("Telegram"), -x[0].published_at.timestamp()))
    return out[:MAX_CANDIDATES]


def _line(it: NewsItem, info: dict) -> str:
    bits = [f"[{it.id}] {cal.to_ist(it.published_at):%H:%M} {it.source}: {it.title[:220]}"]
    if info["symbols"]:
        bits.append(f"stocks: {', '.join(info['symbols'][:5])}")
    if info.get("verdict"):
        bits.append(f"Syena AI: {info['verdict']}" + (f" ({info['summary'][:140]})" if info.get("summary") else ""))
    if info.get("macro"):
        bits.append(f"broad news: {info['macro']}")
    if not info["symbols"] and not info.get("macro") and it.body:
        bits.append(it.body[:160].replace("\n", " "))
    return " | ".join(bits)


def _moves(db: Session, symbols: set[str]) -> dict[str, float]:
    out = {}
    for r in db.scalars(select(StockDaily).where(StockDaily.symbol.in_(symbols)).order_by(StockDaily.day)):
        if r.change_pct is not None:
            out[r.symbol] = r.change_pct  # ordered by day: the latest wins
    return out


def build(db: Session, md=None, now: datetime | None = None, notify: bool = True) -> dict:
    now = now or utcnow()
    ist = cal.to_ist(now)
    day = ist.date()
    end = now
    start = datetime.combine(day - timedelta(days=1), datetime.min.time().replace(hour=18), cal.IST)
    cands = _candidates(db, start, end)
    report = {"day": day.isoformat(), "at": now.isoformat(), "window": {"from": start.isoformat(), "to": end.isoformat()},
              "considered": len(cands), "top": [], "market_mood": None}
    if cands:
        market = "unavailable"
        if md is not None:
            try:
                snap = md.index_snapshot() or {}
                market = "; ".join(f"{k} {v.get('change_pct', 0):+.2f}%" for k, v in snap.items() if v) or "unavailable"
            except Exception:
                pass
        res = llm.call(db, "top_news", "top_news", tier="analysis", max_tokens=2600, temperature=0, variables={
            "day": day.isoformat(), "market": market,
            "items": untrusted("\n".join(_line(it, info) for it, info in cands), "news"),
        })
        d = res.data or {}
        by_id = {it.id: (it, info) for it, info in cands}
        seen = set()
        for t in d.get("top") or []:
            try:
                iid = int(t.get("id"))
            except (TypeError, ValueError):
                continue
            if iid not in by_id or iid in seen:
                continue  # never show an item the AI invented
            seen.add(iid)
            it, info = by_id[iid]
            syms = [s for s in (t.get("symbols") or []) if isinstance(s, str)][:6]
            report["top"].append({
                "rank": len(report["top"]) + 1, "news_id": it.id, "headline": str(t.get("headline") or it.title)[:200],
                "why": str(t.get("why") or "")[:500], "direction": t.get("direction") if t.get("direction") in ("positive", "negative", "mixed") else "mixed",
                "impact": t.get("impact") if t.get("impact") in ("high", "medium", "low") else "medium",
                "symbols": syms, "linked": info["symbols"][:6], "title": it.title[:300], "source": it.source, "url": it.url,
                "published_at": it.published_at.isoformat(), "syena_verdict": info.get("verdict"), "status": it.status,
            })
            if len(report["top"]) >= 10:
                break
        report["market_mood"] = str(d.get("market_mood") or "")[:300] or None
        report["model"], report["cost_usd"] = res.model, res.cost_usd
        moves = _moves(db, {s for t in report["top"] for s in t["linked"] + t["symbols"]})
        for t in report["top"]:
            t["moves"] = {s: moves[s] for s in t["linked"] + t["symbols"] if s in moves}
    _save(db, report)
    if notify and report["top"]:
        _telegram(db, report)
    return report


def _save(db: Session, report: dict) -> None:
    row = db.get(AppSetting, STATE_KEY)
    days = dict((row.value or {}).get("days", {})) if row else {}
    days[report["day"]] = report
    days = dict(sorted(days.items())[-KEEP_DAYS:])
    if row is None:
        db.add(AppSetting(key=STATE_KEY, value={"days": days}, updated_at=utcnow()))
    else:
        row.value, row.updated_at = {"days": days}, utcnow()
    db.commit()


def get(db: Session, day: str | None = None) -> dict:
    row = db.get(AppSetting, STATE_KEY)
    days = (row.value or {}).get("days", {}) if row else {}
    if not days:
        return {"days": [], "report": None}
    key = day if day in days else max(days)
    return {"days": sorted(days, reverse=True), "report": days[key]}


def _telegram(db: Session, report: dict) -> None:
    from syena.config import get_settings
    from syena.notify import telegram

    try:
        icon = {"positive": "🟢", "negative": "🔴", "mixed": "🟡"}
        lines = [f"🗞 <b>Top news, {report['day']}</b>"]
        if report.get("market_mood"):
            lines.append(telegram.esc(report["market_mood"]))
        for t in report["top"]:
            syms = ", ".join(t["symbols"] or t["linked"])
            lines.append(f"{t['rank']}. {icon.get(t['direction'], '⚪')} {telegram.esc(t['headline'])}" + (f" <i>({telegram.esc(syms)})</i>" if syms else ""))
        lines.append(f"{get_settings().public_origin}/top-news")
        telegram.notify(db, "top_news", "\n".join(lines))
    except Exception as e:  # the page matters more than the message
        db.rollback()
        log.warning("top news telegram failed", extra={"error": str(e)[:200]})
