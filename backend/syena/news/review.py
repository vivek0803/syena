"""Daily missed-moves review: the day's biggest gainers and losers, and what Syena's news pipeline did about each.

For every big mover (liquid stocks, at least min_move % up or down) it finds the news Syena had on that stock since
the previous close and how far that news got: traded in the right direction, analysed but rejected (and by which
check), read the wrong way by the AI, dropped before the AI (too old, judged irrelevant, filtered, duplicate), or no
news at all in Syena's sources. A move with no news is compared with its sector and the whole market, so a
sector-wide move isn't mistaken for missed company news. A short AI summary then names the patterns worth fixing.
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.db import utcnow
from syena.market import calendar as cal
from syena.models import AppSetting, Decision, DecisionType, NewsAnalysis, NewsItem, Stock, StockDaily, Trade, TradeReview

log = logging.getLogger("syena.review")

STATE_KEY = "_missed_moves"
KEEP_DAYS = 30
CLASSES = {
    "traded_right": "traded, right direction",
    "traded_late": "traded, but late (after most of the move)",
    "traded_wrong": "traded, wrong direction",
    "rejected": "caught, but a check rejected it",
    "misread": "AI read the news the wrong way",
    "error": "error while processing",
    "dropped": "news dropped before the AI",
    "sector": "no company news: sector-wide move",
    "market": "no company news: whole market moved",
    "no_news": "no news in Syena's sources",
}
DROP_REASON = {"STALE": "arrived too late to trade", "IRRELEVANT": "first screen judged it not price-relevant",
               "FILTERED": "too small / illiquid for the trade gate", "DUPLICATE": "counted as a copy of another story",
               "FAILED": "processing failed twice", "ERROR": "processing failed", "NEW": "still waiting in the queue",
               "TRIAGED": "relevant, but never analysed"}


def _window(day: date, holidays: list[str]) -> tuple[datetime, datetime]:
    prev = day - timedelta(days=1)
    while not cal.is_trading_day(prev, holidays):
        prev -= timedelta(days=1)
    return datetime.combine(prev, cal.CLOSE, cal.IST), datetime.combine(day, cal.CLOSE, cal.IST)


def _news_for(db: Session, sym: str, aliases: dict, start: datetime, end: datetime, items: list[NewsItem], stages: dict) -> list[NewsItem]:
    names = [n for n in aliases.get(sym, []) if len(n) >= 4]
    out = []
    for it in items:
        if any(sym in (a.symbols or []) for a in stages.get(it.id, [])):
            out.append(it)
            continue
        text = f" {it.title.lower()} "
        if f" {sym.lower()} " in text or any(f" {n} " in text for n in names):
            out.append(it)
    return out


LATE_SHARE = 0.6  # a trade entered after 60%+ of the day's move had already happened counts as late


def _trace(db: Session, sym: str, up: bool, news: list[NewsItem], stages: dict, prev_close: float | None = None,
           close: float | None = None) -> tuple[str, str, list[dict]]:
    """Classify what happened, with a one-line reason and the evidence."""
    evidence, best = [], None
    rank = ["traded_right", "traded_late", "rejected", "traded_wrong", "misread", "error", "dropped"]
    for it in news:
        st = stages.get(it.id, [])
        ana = [a for a in st if a.stage == "analysis" and sym in (a.symbols or [])]
        ev = {"news_id": it.id, "title": it.title[:200], "source": it.source, "status": it.status, "published_at": it.published_at.isoformat()}
        cls, why = "dropped", DROP_REASON.get(it.status, it.status.lower())
        tri = next((a for a in st if a.stage == "triage"), None)
        if it.status == "IRRELEVANT" and tri and tri.summary:
            why = f"first screen: {tri.summary[:140]}"
        for a in ana:
            r = a.result or {}
            action = r.get("action")
            ev.update({"action": action, "confidence": r.get("confidence"), "event_type": a.event_type})
            dec = db.scalar(select(Decision).where(Decision.signal_id == r.get("signal_id"))) if r.get("signal_id") else None
            if dec is not None:
                ev["decision_id"] = dec.id
            right_dir = (action == "BUY") if up else (action == "SELL")
            if dec is not None and dec.type == DecisionType.PLACED:
                cls = "traded_right" if (dec.side == "BUY") == up else "traded_wrong"
                why = dec.summary[:160]
                trade = db.get(Trade, dec.trade_id) if dec.trade_id else None
                if cls == "traded_right" and trade and trade.entry_price and prev_close and close and close != prev_close:
                    share = (trade.entry_price - prev_close) / (close - prev_close)  # how much of the day's move was gone at entry
                    ev["entry_share_of_move"] = round(share, 2)
                    if share >= LATE_SHARE:
                        cls = "traded_late"
                        why = (f"bought at ₹{trade.entry_price:,.2f} after {min(share, 1) * 100:.0f}% of the day's move had already happened "
                               f"(previous close ₹{prev_close:,.2f}, close ₹{close:,.2f})")
            elif dec is not None and dec.type == DecisionType.ERROR:
                cls, why = "error", dec.summary[:160]
            elif right_dir:
                fail = next((c for c in (dec.checks_json or []) if not c.get("passed")), None) if dec else None
                cls = "rejected"
                ev["failed_check"] = fail["name"] if fail else None
                why = f"AI said {action} {r.get('confidence')}; rejected by {fail['name'].replace('_', ' ')}: {fail.get('detail', '')[:120]}" if fail \
                    else f"AI said {action} {r.get('confidence')} but no trade"
            else:
                cls, why = "misread", f"AI said {action or 'HOLD'} {r.get('confidence') or ''}: {(a.summary or '')[:120]}"
        ev.update({"class": cls, "why": why})
        evidence.append(ev)
        if best is None or rank.index(cls) < rank.index(best[0]):
            best = (cls, why)
    return (best[0], best[1], evidence) if best else ("no_news", "", [])


def build(db: Session, day: date | None = None, n: int = 10, min_move: float = 4.0, use_ai: bool = True) -> dict:
    from syena.news import mapping
    from syena.screener.screens import latest_day

    holidays = settings_store.get(db, "holidays")
    day = day or latest_day(db)
    if day is None:
        raise ValueError("no daily prices yet: the after-close price update hasn't run")
    min_cap = float(settings_store.get(db, "trade_gate").get("min_market_cap_cr") or 0)
    rows = [r for r in db.scalars(select(StockDaily).where(StockDaily.day == day, StockDaily.change_pct.is_not(None)))]
    if not rows:
        raise ValueError(f"no price changes stored for {day}")
    breadth = median(r.change_pct for r in rows)
    stocks = {s.symbol: s for s in db.scalars(select(Stock).where(Stock.symbol.in_([r.symbol for r in rows])))}
    by_sector = defaultdict(list)
    for r in rows:
        if stocks.get(r.symbol) and stocks[r.symbol].sector:
            by_sector[stocks[r.symbol].sector].append(r.change_pct)
    sector_move = {k: median(v) for k, v in by_sector.items() if len(v) >= 4}
    liquid = [r for r in rows if (r.market_cap_cr or 0) >= min_cap]
    gainers = sorted([r for r in liquid if r.change_pct >= min_move], key=lambda r: -r.change_pct)[:n]
    losers = sorted([r for r in liquid if r.change_pct <= -min_move], key=lambda r: r.change_pct)[:n]

    start, end = _window(day, holidays)
    items = list(db.scalars(select(NewsItem).where(NewsItem.published_at >= start, NewsItem.published_at <= end)))
    stages = defaultdict(list)
    if items:
        for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.news_item_id.in_([i.id for i in items]))):
            stages[a.news_item_id].append(a)
    try:
        aliases = mapping.instrument_aliases(db)
    except Exception:
        aliases = {}

    def mover(r: StockDaily, up: bool) -> dict:
        sec = stocks[r.symbol].sector if stocks.get(r.symbol) else None
        news = _news_for(db, r.symbol, aliases, start, end, items, stages)
        prev = r.close / (1 + r.change_pct / 100) if r.close and r.change_pct is not None else None
        cls, why, ev = _trace(db, r.symbol, up, news, stages, prev, r.close)
        if cls == "no_news":
            sm = sector_move.get(sec) if sec else None
            if sm is not None and abs(sm) >= 1.5 and (sm > 0) == up:
                cls, why = "sector", f"{sec} moved {sm:+.1f}% (median): likely sector news or flows, not company news"
            elif abs(breadth) >= 1.0 and (breadth > 0) == up:
                cls, why = "market", f"the whole market moved {breadth:+.1f}% (median stock)"
            else:
                why = "no article in Syena's sources: possibly a BSE-only filing, a block/bulk deal, broker upgrade, social media or a purely technical move"
        return {"symbol": r.symbol, "name": stocks[r.symbol].name if stocks.get(r.symbol) else None, "sector": sec,
                "change_pct": round(r.change_pct, 2), "close": r.close, "market_cap_cr": r.market_cap_cr,
                "class": cls, "class_label": CLASSES[cls], "why": why, "news": ev[:5]}

    report = {"day": day.isoformat(), "built_at": utcnow().isoformat(), "min_move_pct": min_move, "min_market_cap_cr": min_cap,
              "market_median_pct": round(breadth, 2), "stocks_priced": len(rows),
              "gainers": [mover(r, True) for r in gainers], "losers": [mover(r, False) for r in losers]}
    counts = defaultdict(int)
    for m in report["gainers"] + report["losers"]:
        counts[m["class"]] += 1
    report["counts"] = dict(counts)
    report["lessons"] = _ai_lessons(db, report) if use_ai and (report["gainers"] or report["losers"]) else None
    try:
        report["learning"] = feed_learning(db, report)
    except Exception as e:  # learning must never break the review
        db.rollback()
        log.warning("missed moves not fed to learning", extra={"error": str(e)})
        report["learning"] = None
    _save(db, report)
    return report


def feed_learning(db: Session, report: dict) -> dict:
    """Turn the day's outcomes into evidence for lessons (Learning page). Lessons only take effect after enough
    evidence across several days, a test on later data and your approval, so one odd day changes nothing.
      held back (score under threshold / AI said hold) on news that then moved the stock -> 'give this news type more weight'
      traded the wrong way                                                               -> 'be more careful with this news type'
      bought late, after the move                                                         -> 'news buys after a 6%+ five-day run come late'
      traded right                                                                        -> supports the 'more weight' lesson if one exists
    Rejections by risk checks (priced in, already moved, liquidity) are left alone: lessons never loosen risk rules."""
    from syena.learning import lessons as L

    day = date.fromisoformat(report["day"])
    out = {"evidence": 0, "lessons": []}

    def review_row(m: dict) -> TradeReview:
        for tr in db.scalars(select(TradeReview).where(TradeReview.kind == "missed_move")):
            cf = tr.counterfactual or {}
            if cf.get("day") == report["day"] and cf.get("symbol") == m["symbol"]:
                return tr
        tr = TradeReview(kind="missed_move", classification=m["class"][:32],
                         decision_quality="sound" if m["class"] == "traded_right" else "unsound",
                         outcome="win" if m["class"] == "traded_right" else "missed_gain",
                         counterfactual={"day": report["day"], "symbol": m["symbol"], "change_pct": m["change_pct"]},
                         summary=f"{m['symbol']} {m['change_pct']:+.1f}%: {m['class_label']}. {m['why'][:200]}")
        db.add(tr)
        db.flush()
        return tr

    def evidence(text, trigger, action, supports, tr, create=True):
        lesson = None
        if create:
            lesson = L.create_proposed(db, text, trigger, action, source="review")
        else:
            trig = L.validate_trigger(trigger)
            lesson = next((x for x in db.scalars(select(L.Lesson).where(L.Lesson.status != "RETIRED"))
                           if x.trigger == trig and x.action["type"] == action["type"]), None)
        if lesson is None:
            return
        L.add_evidence(db, lesson, supports, None, tr.id, day, note=f"daily review {report['day']}")
        out["evidence"] += 1
        if lesson.code not in out["lessons"]:
            out["lessons"].append(lesson.code)

    movers = report["gainers"] + report["losers"]
    creates = ("misread", "rejected", "traded_wrong", "traded_late")
    for m in sorted(movers, key=lambda m: m["class"] not in creates):  # lessons first, supporting evidence after
        ev = next((e for e in m["news"] if e.get("action")), None)
        et = (ev or {}).get("event_type")
        cls = m["class"]
        if cls not in ("rejected", "misread", "traded_right", "traded_late", "traded_wrong"):
            continue
        tr = review_row(m)
        more = ({"source": "news", "event_types": [et]}, {"type": "prefer", "value": 5}) if et else None
        if cls == "misread" or (cls == "rejected" and (ev or {}).get("failed_check") == "confidence"):
            if more:
                evidence(f"News about '{et.replace('_', ' ')}' that Syena held back on (score under the threshold or AI said hold) "
                         f"went on to move stocks 4%+ in the news direction: give it more weight", *more, True, tr)
        elif cls == "traded_right" and more:
            evidence("", *more, True, tr, create=False)
        elif cls == "traded_wrong" and et:
            evidence(f"News trades on '{et.replace('_', ' ')}' went the wrong way on big moves: be more careful",
                     {"source": "news", "event_types": [et]}, {"type": "reduce_confidence", "value": 10}, True, tr)
            if more:
                evidence("", *more, False, tr, create=False)
        elif cls == "traded_late" and m["change_pct"] > 0:
            evidence("News buys made after the stock had already risen 6%+ over five days came too late to profit",
                     {"source": "news", "ranges": {"ret_5": [6.0, None]}}, {"type": "reduce_confidence", "value": 10}, True, tr)
    db.commit()
    return out


def _ai_lessons(db: Session, report: dict) -> str | None:
    import json

    from syena.ai import llm
    from syena.ai.prompts import untrusted

    compact = [{k: m[k] for k in ("symbol", "sector", "change_pct", "class", "why")} for m in report["gainers"] + report["losers"]]
    try:
        res = llm.call(db, "missed_moves", "missed_moves", tier="analysis", max_tokens=700, want_json=False, variables={
            "day": report["day"], "market": f"median stock {report['market_median_pct']:+.2f}%",
            "movers": untrusted(json.dumps(compact, ensure_ascii=False), "review")})
        return (res.text or "").strip()[:2500] or None
    except Exception as e:
        log.info("missed-moves summary skipped", extra={"error": str(e)})
        return None


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


def telegram_summary(report: dict) -> str:
    c = report["counts"]
    caught = c.get("traded_right", 0)
    total = len(report["gainers"]) + len(report["losers"])
    lines = [f"📊 <b>Missed-moves review {report['day']}</b>",
             f"{total} big movers (±{report['min_move_pct']:g}%+): {caught} traded right, {c.get('traded_late', 0)} traded late, {c.get('rejected', 0)} caught but rejected, "
             f"{c.get('misread', 0)} misread, {c.get('dropped', 0)} dropped early, {c.get('no_news', 0)} with no news, "
             f"{c.get('sector', 0) + c.get('market', 0)} sector/market moves."]
    if report.get("lessons"):
        lines.append(report["lessons"][:700])
    return "\n".join(lines)
