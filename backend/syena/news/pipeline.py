"""News-driven pipeline (spec 7): sources -> dedupe -> mapping/triage -> AI analysis -> signal -> trade gate.

`analyse()` is point-in-time pure: it only sees data available at `as_of` (market data is cut by the caller's
FrameMarketData in replay; lessons/similar trades are filtered by timestamp). The live path and the replay
engine both use it, so evals measure exactly what trades.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import events, settings_store
from syena.ai import llm
from syena.ai.prompts import untrusted
from syena.db import utcnow
from syena.execution import engine
from syena.execution.checks import Check
from syena.learning import pretrade
from syena.learning.features import build_setup
from syena.models import Algo, NewsAnalysis, NewsItem, Signal, Style, Trade, TradeStatus, TradingMode
from syena.news import mapping, sources
from syena.news.context import market_context, technicals
from syena.services import mode as mode_service

log = logging.getLogger("syena.news")


# ---------------------------------------------------------------- ingest + dedupe
def ingest(db: Session, raw: list[sources.RawItem]) -> list[NewsItem]:
    max_age = timedelta(hours=settings_store.get(db, "news")["max_age_hours"])
    recent = list(db.scalars(select(NewsItem).where(NewsItem.published_at >= utcnow() - timedelta(hours=48))))
    new = []
    for r in raw:
        h = sources.content_hash(r.title)
        if db.scalar(select(NewsItem.id).where(NewsItem.content_hash == h)):
            continue
        item = NewsItem(source=r.source, url=r.url[:1024], title=r.title[:1000], body=r.body, published_at=r.published_at, content_hash=h)
        dup = next((x for x in recent if sources.jaccard(x.title, r.title) >= 0.8), None)
        if dup:
            item.status, item.dup_of_id = "DUPLICATE", dup.id
        elif utcnow() - r.published_at > max_age:
            item.status = "IRRELEVANT"  # stale: stored for the record/replay, not traded
        db.add(item)
        db.flush()
        recent.append(item)
        new.append(item)
    db.commit()
    return new


def item_text(item: NewsItem) -> str:
    return f"[{item.id}] {item.published_at.isoformat()} {item.source}: {item.title}\n{(item.body or '')[:1500]}"


# ---------------------------------------------------------------- triage
def triage(db: Session, item: NewsItem, extra_symbols: list[str], *, model: str | None = None, eval_run_id: int | None = None,
           persist: bool = True) -> dict:
    cands = mapping.candidates(db, f"{item.title} {item.body or ''}", extra_symbols)
    if not cands:
        result = {"relevant": False, "symbols": [], "event_type": None, "reason": "no listed company mentioned"}
        ai_id = mdl = pv = None
    else:
        res = llm.call(db, "news_triage", "news_triage", tier=None if model else "triage", model=model, max_tokens=300,
                       eval_run_id=eval_run_id,
                       variables={"candidates": ", ".join(f"{s}: {n}" for s, n in cands.items()),
                                  "item": untrusted(item_text(item), "news")})
        result, ai_id, mdl, pv = res.data or {}, res.ai_call_id, res.model, res.prompt_version
        result["symbols"] = [s for s in result.get("symbols", []) if s in cands]  # never trust symbols outside candidates
        result["relevant"] = bool(result.get("relevant")) and bool(result["symbols"])
    if persist:
        db.add(NewsAnalysis(news_item_id=item.id, stage="triage", relevant=result["relevant"], symbols=result["symbols"],
                            event_type=result.get("event_type"), summary=result.get("reason"), result=result,
                            model=mdl, prompt_version=pv, ai_call_id=ai_id))
        item.status = "TRIAGED" if result["relevant"] else "IRRELEVANT"
        db.commit()
    return result


# ---------------------------------------------------------------- analysis (point-in-time)
def analyse(db: Session, symbol: str, items: list[NewsItem], md, as_of: datetime, *, event_type: str | None = None,
            model: str | None = None, shadow: bool = False, eval_run_id: int | None = None) -> dict:
    daily = md.candles(symbol, "day", as_of - timedelta(days=400), as_of)
    daily = daily[daily.index <= as_of] if len(daily) else daily  # belt and braces against lookahead
    tech = technicals(daily)
    ctx = market_context(md.index_snapshot() or {}, settings_store.get(db, "market_context"))
    setup = build_setup(symbol=symbol, style=settings_store.get(db, "news")["style"], source="news", ts=as_of, df=daily,
                        event_type=event_type, vix=ctx["raw"].get("vix"), market_context=ctx["score"])
    consult = pretrade.consult(db, setup, None, as_of=as_of)
    active = [l for l in pretrade.retrieve(db, setup, statuses=("ACTIVE",), as_of=as_of)]
    news_txt = untrusted("\n\n".join(item_text(i) for i in items if i.published_at <= as_of), "news")
    res = llm.call(db, "news_analysis", "news_analysis", tier=None if model else "analysis", model=model, max_tokens=900,
                   shadow=shadow, eval_run_id=eval_run_id,
                   variables={"symbol": symbol, "news": news_txt, "technicals": json.dumps(tech),
                              "context": json.dumps(ctx), "lessons": pretrade.lessons_context(active),
                              "similar": untrusted(json.dumps(consult.similar, default=str)[:2500], "similar_trades")})
    d = res.data or {}
    action = str(d.get("action", "HOLD")).upper()
    action = action if action in ("BUY", "SELL", "HOLD") else "HOLD"
    try:
        conf = max(0.0, min(100.0, float(d.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    valid_ids = {i.id for i in items}
    cited = [c for c in (d.get("cited") or []) if isinstance(c, int) and c in valid_ids]
    price = float(daily["close"].iloc[-1]) if len(daily) else None
    ltp = md.ltp([symbol]).get(symbol)
    return {"symbol": symbol, "action": action, "confidence": conf, "sentiment": d.get("sentiment"),
            "summary": d.get("summary"), "reasoning": d.get("reasoning"), "cited": cited, "risks": d.get("risks"),
            "llm_technicals_agree": d.get("technicals_agree"), "technicals": tech, "context": ctx, "setup": setup,
            "consult": consult, "model": res.model, "prompt_version": res.prompt_version, "ai_call_id": res.ai_call_id,
            "price": ltp or price, "as_of": as_of.isoformat()}


def gate_checks(db: Session, a: dict, confidence_after_lessons: float) -> list[Check]:
    g = settings_store.get(db, "trade_gate")
    tech, ctx = a["technicals"], a["context"]
    checks = [Check("signal_action", a["action"] == "BUY",
                    f"action {a['action']}" + ("" if a["action"] == "BUY" else " (long-only: only BUY opens positions)")),
              Check("confidence", confidence_after_lessons >= g["confidence_threshold"],
                    f"confidence {confidence_after_lessons:g} vs threshold {g['confidence_threshold']}"
                    + (f" (model said {a['confidence']:g})" if confidence_after_lessons != a["confidence"] else ""))]
    if g["require_technical_agreement"]:
        ok = not tech.get("available") or not tech.get("bearish")
        checks.append(Check("technicals", ok, "; ".join(tech.get("reasons", [])) or "no technical data", {"score": tech.get("score")}))
    if ctx.get("available"):
        checks.append(Check("market_context", ctx["score"] >= g["min_market_context"],
                            f"context score {ctx['score']} (min {g['min_market_context']})", ctx["components"]))
    return checks


def news_algo(db: Session) -> Algo:
    a = db.scalar(select(Algo).where(Algo.kind == "news"))
    if a is None:
        style = Style(settings_store.get(db, "news")["style"])
        a = Algo(name="News strategy", kind="news", english_text="AI news-driven signals (see Decision Log)", style=style,
                 run_mode="PAPER", status="RUNNING", symbols=[], timeframe="day")
        db.add(a)
        db.commit()
    return a


def second_opinion(db: Session, symbol: str, items, md, as_of, primary: dict) -> Check | None:
    model = settings_store.get(db, "models").get("second_opinion")
    if not model or mode_service.current_mode(db) != TradingMode.LIVE:
        return None
    try:
        b = analyse(db, symbol, items, md, as_of, model=model)
    except Exception as e:
        return Check("second_opinion", False, f"second opinion failed: {e}")
    thr = settings_store.get(db, "trade_gate")["confidence_threshold"]
    agree = b["action"] == primary["action"] and b["confidence"] >= thr
    return Check("second_opinion", agree, f"{model}: {b['action']} {b['confidence']:g} vs primary {primary['action']} {primary['confidence']:g}")


def process_symbol(db: Session, symbol: str, items: list[NewsItem], md, event_type: str | None, broker_factory=None,
                   now: datetime | None = None) -> Signal:
    now = now or utcnow()
    a = analyse(db, symbol, items, md, now, event_type=event_type)
    consult = a["consult"]
    conf_after = a["confidence"]
    # re-run the lesson consult now that we know the model's confidence
    consult = pretrade.consult(db, {**a["setup"], "technicals_agree": a["technicals"].get("bullish", False)}, a["confidence"])
    conf_after = consult.confidence if consult.confidence is not None else a["confidence"]
    algo = news_algo(db)
    sig = Signal(source="news", algo_id=algo.id, symbol=symbol, action=a["action"], confidence=a["confidence"],
                 reasoning=a["reasoning"], cited=a["cited"], indicators=a["technicals"], market_context=a["context"],
                 model=a["model"], prompt_version=a["prompt_version"], price_at_signal=a["price"], ts=now)
    db.add(sig)
    db.flush()
    db.add(NewsAnalysis(news_item_id=items[0].id, stage="analysis", relevant=True, symbols=[symbol],
                        sentiment=a["sentiment"] if isinstance(a["sentiment"], (int, float)) else None, event_type=event_type,
                        summary=a["summary"], result={k: v for k, v in a.items() if k not in ("consult",)} | {
                            "signal_id": sig.id, "inputs": {"news": [{"id": i.id, "title": i.title} for i in items]}},
                        model=a["model"], prompt_version=a["prompt_version"], ai_call_id=a["ai_call_id"]))
    for it in items:
        it.status = "ANALYSED"
    db.commit()
    _shadow(db, symbol, items, md, now, event_type)

    held = db.scalar(select(Trade).where(Trade.stock == symbol, Trade.status == TradeStatus.OPEN))
    if a["action"] == "SELL" and held:
        events.publish(db, "news_sell_signal", {"symbol": symbol, "signal_id": sig.id, "trade_id": held.id})
        return sig  # the position monitor turns this into a Telegram SELL alert
    checks = gate_checks(db, a, conf_after) + consult.checks
    so = second_opinion(db, symbol, items, md, now, a) if a["action"] == "BUY" else None
    if so:
        checks.append(so)
    if a["price"] is None:
        checks.append(Check("price", False, "no price available"))
    setup = {**a["setup"], "confidence": a["confidence"], "technicals_agree": a["technicals"].get("bullish", False)}
    intent = engine.TradeIntent(
        symbol=symbol, style=algo.style, source="news", ref_price=a["price"] or 0.0, algo_id=algo.id, signal_id=sig.id,
        confidence=conf_after, idempotency_key=f"news-sig{sig.id}", extra_checks=checks, auto_sell=True,
        reasoning={"summary": a["summary"], "reasoning": a["reasoning"], "risks": a["risks"], "cited": a["cited"],
                   "model": a["model"], "prompt_version": a["prompt_version"], "model_confidence": a["confidence"]},
        inputs={"news": [{"id": i.id, "title": i.title, "source": i.source, "published_at": i.published_at.isoformat()} for i in items],
                "technicals": a["technicals"], "market_context": a["context"]},
        lessons_consulted=consult.consulted, lessons_applied=consult.applied, lessons_would_apply=consult.would_apply,
        similar_trades=consult.similar, news_item_ids=[i.id for i in items], setup=setup,
    )
    if a["price"]:
        from syena.algos.service import _risk_prices

        daily = md.candles(symbol, "day", now - timedelta(days=120), now)
        if len(daily) > 20:
            intent.stop_loss, intent.target = _risk_prices({}, daily, a["price"], algo.style, db, consult.stop_factor)
    d = engine.enter(db, intent, broker_factory, now=now)
    pretrade.record(db, d.id, consult)
    return sig


def _shadow(db: Session, symbol, items, md, now, event_type) -> None:
    """Challenger model runs in parallel: logged and scored, never traded (17G shadow mode)."""
    model = settings_store.get(db, "models").get("shadow_analysis")
    if not model:
        return
    try:
        b = analyse(db, symbol, items, md, now, event_type=event_type, model=model, shadow=True)
        db.add(Signal(source="news", symbol=symbol, action=b["action"], confidence=b["confidence"], reasoning=b["reasoning"],
                      cited=b["cited"], model=b["model"], prompt_version=b["prompt_version"], shadow=True,
                      price_at_signal=b["price"], ts=now))
        db.commit()
    except Exception as e:
        log.warning("shadow analysis failed", extra={"stock": symbol, "error": str(e)})


def run_cycle(db: Session, md, fetch=sources.fetch_all, broker_factory=None) -> dict:
    """One polling cycle: fetch, dedupe, triage, analyse (grouped per stock), gate."""
    cfg = settings_store.get(db, "news")
    new = ingest(db, fetch(cfg["feeds"]))
    held = [t.stock for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN))]
    extra = list(dict.fromkeys(cfg["watchlist"] + held))
    pending = list(db.scalars(select(NewsItem).where(NewsItem.status == "NEW").order_by(NewsItem.published_at).limit(cfg["max_items_per_run"])))
    by_symbol: dict[str, tuple[list[NewsItem], str | None]] = {}
    stats = {"fetched": len(new), "triaged": 0, "relevant": 0, "signals": 0, "errors": 0}
    for item in pending:
        try:
            t = triage(db, item, extra)
            stats["triaged"] += 1
            if t["relevant"]:
                stats["relevant"] += 1
                for s in t["symbols"]:
                    lst, _ = by_symbol.get(s, ([], None))
                    by_symbol[s] = (lst + [item], t.get("event_type"))
        except (llm.BudgetExceeded, llm.LLMUnavailable) as e:
            log.warning("news triage paused", extra={"reason": str(e)})
            break
        except Exception as e:
            item.status = "ERROR"
            db.commit()
            stats["errors"] += 1
            log.error("triage failed", extra={"news_id": item.id, "error": str(e)})
    for sym, (items, etype) in by_symbol.items():
        try:
            process_symbol(db, sym, items, md, etype, broker_factory)
            stats["signals"] += 1
        except (llm.BudgetExceeded, llm.LLMUnavailable) as e:
            log.warning("news analysis paused", extra={"reason": str(e)})
            break
        except Exception as e:
            stats["errors"] += 1
            log.error("analysis failed", extra={"stock": sym, "error": str(e)})
    if any(stats.values()):
        events.publish(db, "news_cycle", stats)
    return stats
