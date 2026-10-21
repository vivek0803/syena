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
from syena.models import Algo, DecisionType, NewsAnalysis, NewsItem, Signal, Style, Trade, TradeStatus, TradingMode
from syena.news import mapping, sources
from syena.news.context import market_context, technicals
from syena.services import mode as mode_service

log = logging.getLogger("syena.news")


# ---------------------------------------------------------------- ingest + dedupe
def max_age(db: Session) -> timedelta:
    cfg = settings_store.get(db, "news")
    return timedelta(minutes=float(cfg.get("max_age_minutes") or 15))


def ingest(db: Session, raw: list[sources.RawItem], now: datetime | None = None) -> list[NewsItem]:
    """Store new items. Exact repeats are dropped; the same story from another source is marked DUPLICATE;
    anything older than max_age_minutes is marked STALE. Only NEW items ever reach the AI."""
    now = now or utcnow()
    limit = max_age(db)
    recent = list(db.scalars(select(NewsItem).where(NewsItem.fetched_at >= now - timedelta(hours=48))))
    new = []
    for r in sorted(raw, key=lambda x: x.published_at):
        h = sources.content_hash(r.title, r.key)
        if db.scalar(select(NewsItem.id).where(NewsItem.content_hash == h)):
            continue
        published = min(r.published_at, now)  # a future timestamp (bad feed timezone) counts as "just now"
        story = f"{r.title} {r.body[:200]}" if r.key and r.body else r.title
        item = NewsItem(source=r.source[:64], url=r.url[:1024], title=r.title[:1000], body=r.body, published_at=published,
                        content_hash=h, image_urls=r.images or None, fetched_at=now)
        # compared with STALE items too: a story first seen 20 minutes ago is old news even if another site posts it now
        dup = next((x for x in recent if (sources.same_story(x.title, r.title) or
                    (r.key and sources.same_story(f"{x.title} {(x.body or '')[:200]}", story)))), None)
        routine = False
        if dup:
            item.status, item.dup_of_id = "DUPLICATE", dup.dup_of_id or dup.id
        elif now - published > limit:
            item.status = "STALE"  # stored for the record, never sent to the AI or traded
        elif sources.is_routine(r.title, r.body):
            item.status, routine = "IRRELEVANT", True  # routine exchange paperwork: no AI call
        db.add(item)
        db.flush()
        if routine:
            db.add(NewsAnalysis(news_item_id=item.id, stage="triage", relevant=False, symbols=[], event_type="other",
                                summary="Routine filing (trading window, certificates, meeting notices…): skipped without an AI call",
                                result={"routine": True}))
        recent.append(item)
        new.append(item)
    db.commit()
    return new


def fair_queue(items: list[NewsItem], limit: int) -> list[NewsItem]:
    """Newest first, taking turns between sources, so one busy feed (NSE posts dozens of filings at a time) can't
    keep other sources waiting until their news goes stale."""
    by_src: dict[str, list[NewsItem]] = {}
    for it in sorted(items, key=lambda x: x.published_at, reverse=True):
        by_src.setdefault(it.source, []).append(it)
    out: list[NewsItem] = []
    while len(out) < limit and any(by_src.values()):
        for lst in by_src.values():
            if lst and len(out) < limit:
                out.append(lst.pop(0))
    return out


def _save_health(db: Session) -> None:
    from syena.models import AppSetting

    if not sources.health:
        return
    row = db.get(AppSetting, "_news_sources") or AppSetting(key="_news_sources", value={})
    val = dict(row.value or {})
    val.update(sources.health)
    row.value, row.updated_at = val, utcnow()
    db.merge(row)
    db.commit()


def read_images(db: Session, item: NewsItem, http_get=None) -> int:
    """Append the text found in a post's images to its body (AI vision, triage model). Returns images read."""
    urls = (item.image_urls or [])[:3]
    if not urls:
        return 0
    get = http_get or sources.download_image
    imgs = []
    for u in urls:
        try:
            got = get(u)
            if got:
                imgs.append(got)
        except Exception as e:
            log.info("image download failed", extra={"news_id": item.id, "error": str(e)})
    if not imgs:
        return 0
    res = llm.call(db, "news_image", "image_reader", tier="triage", max_tokens=400, want_json=False, images=imgs,
                   variables={"text": untrusted((item.body or "")[:1500], "news")})
    text = (res.text or "").strip()
    if text and "NO MARKET CONTENT" not in text.upper():
        item.body = ((item.body or "") + "\n\n[Text read from image]\n" + text).strip()
        if item.title == "[image post]":
            item.title = text.split("\n", 1)[0][:300]
    db.commit()
    return len(imgs)


def item_text(item: NewsItem) -> str:
    limit = 7000 if sources.FILING_MARK in (item.body or "") else 1500  # filings: keep the figures from the PDF
    return f"[{item.id}] {item.published_at.isoformat()} {item.source}: {item.title}\n{(item.body or '')[:limit]}"


# ---------------------------------------------------------------- triage
def triage(db: Session, item: NewsItem, extra_symbols: list[str], *, model: str | None = None, eval_run_id: int | None = None,
           persist: bool = True) -> dict:
    cands = mapping.candidates(db, f"{item.title} {item.body or ''}", extra_symbols)
    if not cands:
        result = {"relevant": False, "symbols": [], "event_type": None, "reason": "no listed company mentioned"}
        ai_id = mdl = pv = None
    else:
        res = llm.call(db, "news_triage", "news_triage", tier=None if model else "triage", model=model, max_tokens=500,
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
    ncfg = settings_store.get(db, "news")
    if tech.get("available") and ncfg.get("pattern_timeframes"):
        from syena.news.context import multi_timeframe

        tech["patterns_by_timeframe"] = multi_timeframe(md, symbol, as_of, ncfg["pattern_timeframes"],
                                                        int(ncfg.get("pattern_lookback_days") or 30))
        for tf in tech["patterns_by_timeframe"]:
            if tf.get("available") and (tf["bullish"] or tf["bearish"]):
                tech["reasons"].append(f"{tf['timeframe']} last {int(ncfg.get('pattern_lookback_days') or 30)}d: "
                                       f"{tf['bullish']} bullish / {tf['bearish']} bearish patterns ({tf['bias']}), {tf['trendline']}")
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
            "price": ltp or price, "as_of": as_of.isoformat(), "liquidity": liquidity(db, symbol, daily)}


def liquidity(db: Session, symbol: str, daily) -> dict:
    """Average daily traded value (last 20 sessions, ₹ crore) and market cap (₹ crore) when known."""
    from syena.models import StockDaily

    out = {"avg_turnover_cr": None, "market_cap_cr": None}
    if daily is not None and len(daily) >= 5:
        tv = (daily["close"].astype(float) * daily["volume"].astype(float)).tail(20).mean() / 1e7
        out["avg_turnover_cr"] = round(float(tv), 2) if tv == tv else None
    row = db.scalar(select(StockDaily).where(StockDaily.symbol == symbol, StockDaily.market_cap_cr.is_not(None))
                    .order_by(StockDaily.day.desc()).limit(1))
    if row is not None:
        out["market_cap_cr"] = round(float(row.market_cap_cr), 0)
    return out


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def technical_points(tech: dict) -> float:
    """How bullish the technicals are, 0-100 (50 = neutral / unknown): trend & momentum score, the month's candle
    pattern bias on the daily and 1-hour charts, and trendline breakouts/breakdowns."""
    if not tech.get("available"):
        return 50.0
    pts = _clip((tech.get("score", 0) + 3) / 6 * 100)  # score runs about -4..+3
    for tf in tech.get("patterns_by_timeframe") or []:
        if tf.get("available") and tf.get("timeframe") in ("daily", "1-hour"):
            pts += {"bullish": 8, "bearish": -8}.get(tf.get("bias"), 0)
            pts += 8 if "breakout" in (tf.get("trendline") or "") else -8 if "breakdown" in (tf.get("trendline") or "") else 0
    return round(_clip(pts), 1)


def weighted_score(db: Session, a: dict, news_confidence: float) -> tuple[float, dict]:
    """Final score for a news trade = news × w_news + technicals × w_tech + market × w_market (default 80/15/5).
    Every part is 0-100 *in the direction of the trade*: for a short, bearish technicals and a falling market score high."""
    w = {**{"news": 80, "technicals": 15, "market": 5}, **(settings_store.get(db, "trade_gate").get("weights") or {})}
    short = a["action"] == "SELL"
    tech = technical_points(a["technicals"])
    ctx = a["context"]
    mkt = _clip((ctx["score"] + 1) / 2 * 100) if ctx.get("available") else 50.0  # score runs -1..+1
    if short:
        tech, mkt = 100 - tech, 100 - mkt
    parts = {"news": round(float(news_confidence), 1), "technicals": round(tech, 1), "market": round(mkt, 1)}
    total = sum(float(w[k]) for k in parts) or 1.0
    score = round(sum(parts[k] * float(w[k]) for k in parts) / total, 1)
    return score, {"parts": parts, "weights": {k: float(w[k]) for k in parts}}


def gate_checks(db: Session, a: dict, confidence_after_lessons: float) -> list[Check]:
    g = settings_store.get(db, "trade_gate")
    tech, ctx = a["technicals"], a["context"]
    short = a["action"] == "SELL"
    score, br = weighted_score(db, a, confidence_after_lessons)
    a["weighted_score"], a["score_breakdown"] = score, br
    p, w = br["parts"], br["weights"]
    formula = " + ".join(f"{k} {p[k]:g}×{w[k]:g}%" for k in ("news", "technicals", "market"))
    checks = [Check("signal_action", a["action"] in ("BUY", "SELL"),
                    {"BUY": "AI verdict BUY: open a long position", "SELL": "AI verdict SELL: sell short intraday, buy back by the close"}
                    .get(a["action"], f"AI verdict {a['action']}")),
              Check("confidence", score >= g["confidence_threshold"],
                    f"weighted score {score:g} vs threshold {g['confidence_threshold']}: {formula}"
                    + (f" (news: model said {a['confidence']:g}, lessons adjusted to {confidence_after_lessons:g})"
                       if confidence_after_lessons != a["confidence"] else ""), br),
              Check("technicals", True, f"{p['technicals']:g}/100 for a {'short' if short else 'buy'} (weight {w['technicals']:g}%): "
                    + ("; ".join(tech.get("reasons", [])) or "no technical data"), {"score": tech.get("score")})]
    liq = a.get("liquidity") or {}
    mcap_min, tv_min = float(g.get("min_market_cap_cr") or 0), float(g.get("min_avg_turnover_cr") or 0)
    if mcap_min or tv_min:
        mcap, tv = liq.get("market_cap_cr"), liq.get("avg_turnover_cr")
        problems = []
        if mcap_min and mcap is not None and mcap < mcap_min:
            problems.append(f"market cap ₹{mcap:,.0f} cr < ₹{mcap_min:,.0f} cr")
        if tv_min and tv is not None and tv < tv_min:
            problems.append(f"average daily turnover ₹{tv:,.2f} cr < ₹{tv_min:g} cr")
        known = ", ".join(x for x in [f"market cap ₹{mcap:,.0f} cr" if mcap is not None else "market cap unknown",
                                       f"turnover ₹{tv:,.2f} cr/day" if tv is not None else "turnover unknown"])
        checks.append(Check("liquidity", not problems, "; ".join(problems) if problems else known))
    checks.append(Check("market_context", True, f"{p['market']:g}/100 for a {'short' if short else 'buy'} (weight {w['market']:g}%): "
                        + (f"Nifty/Sensex/VIX score {ctx['score']}" if ctx.get("available") else "no market data (neutral 50)"),
                        ctx.get("components")))
    return checks


def friendly_error(e: Exception) -> str:
    msg = str(e)
    if "403" in msg and "Angel" in msg:
        return f"price data unavailable: Angel One is limiting requests ({msg})"
    if "not an NSE equity symbol" in msg:
        return f"no price data for this symbol ({msg})"
    if "budget" in msg.lower():
        return f"daily AI budget reached ({msg})"
    return msg[:300] or type(e).__name__


def reject_error(db: Session, symbol: str, items: list, step: str, e: Exception) -> None:
    """A stock that couldn't be processed gets a REJECTED decision saying which step failed and why."""
    try:
        algo = news_algo(db)
        why = friendly_error(e)
        engine.record_verdict(db, engine.TradeIntent(symbol=symbol, style=algo.style, source="news", ref_price=0.0, algo_id=algo.id,
                                                     inputs={"news": [{"id": i.id, "title": i.title} for i in items]},
                                                     news_item_ids=[i.id for i in items]),
                              DecisionType.ERROR, f"ERROR at {step}: {why}", [Check(step, False, why)])
    except Exception as e2:  # recording must never break the news loop
        db.rollback()
        log.warning("could not record rejection", extra={"stock": symbol, "error": str(e2)})


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

    held = db.scalar(select(Trade).where(Trade.stock == symbol, Trade.status == TradeStatus.OPEN, Trade.side == "BUY"))
    if a["action"] == "SELL" and held:
        events.publish(db, "news_sell_signal", {"symbol": symbol, "signal_id": sig.id, "trade_id": held.id})
        return sig  # you hold it: the position monitor turns this into a SELL alert/exit instead of a short
    short = a["action"] == "SELL"
    verdict = Check("ai_verdict", True, f"{a['action']} with confidence {a['confidence']:g}"
                    + (f" (after lessons {conf_after:g})" if conf_after != a["confidence"] else ""))
    base_intent = dict(symbol=symbol, source="news", ref_price=a["price"] or 0.0, algo_id=algo.id, signal_id=sig.id,
                       confidence=conf_after, reasoning={"summary": a["summary"], "reasoning": a["reasoning"], "risks": a["risks"],
                                                         "cited": a["cited"], "model": a["model"], "prompt_version": a["prompt_version"],
                                                         "model_confidence": a["confidence"], "action": a["action"]},
                       inputs={"news": [{"id": i.id, "title": i.title, "source": i.source, "published_at": i.published_at.isoformat()}
                                        for i in items], "technicals": a["technicals"], "market_context": a["context"]},
                       news_item_ids=[i.id for i in items])
    if a["action"] == "HOLD":  # simple outcomes: buy / sell / rejected / error. A HOLD verdict is a rejection with its reason
        engine.record_verdict(db, engine.TradeIntent(style=algo.style, **base_intent), DecisionType.REJECTED,
                              f"REJECTED: AI verdict HOLD (no clear edge): {a['summary'] or ''}".strip(),
                              [Check("ai_verdict", False, f"HOLD with confidence {a['confidence']:g}: {a['summary'] or 'no clear edge'}")])
        return sig
    if short and not settings_store.get(db, "news").get("short_selling", True):
        engine.record_verdict(db, engine.TradeIntent(style=Style.INTRADAY, side="SELL", **base_intent), DecisionType.REJECTED,
                              "REJECTED by short_selling: AI said SELL but short selling is off (Settings → News)",
                              [verdict, Check("short_selling", False, "short selling is turned off in Settings → News")])
        return sig
    checks = gate_checks(db, a, conf_after) + consult.checks
    base_intent["confidence"] = a["weighted_score"]  # what the threshold was compared with
    base_intent["reasoning"] = {**base_intent["reasoning"], "weighted_score": a["weighted_score"], "score_breakdown": a["score_breakdown"]}
    so = second_opinion(db, symbol, items, md, now, a) if a["action"] in ("BUY", "SELL") else None
    if so:
        checks.append(so)
    if a["price"] is None:
        checks.append(Check("price", False, "no price available"))
    setup = {**a["setup"], "confidence": a["confidence"], "technicals_agree": a["technicals"].get("bullish", False)}
    intent = engine.TradeIntent(
        style=Style.INTRADAY if short else algo.style, side="SELL" if short else "BUY",
        idempotency_key=f"news-sig{sig.id}", extra_checks=[verdict, *checks], auto_sell=True,
        lessons_consulted=consult.consulted, lessons_applied=consult.applied, lessons_would_apply=consult.would_apply,
        similar_trades=consult.similar, setup=setup, **base_intent,
    )
    if a["price"]:
        from syena.algos.service import _risk_prices

        daily = md.candles(symbol, "day", now - timedelta(days=120), now)
        if len(daily) > 20:
            stop, target = _risk_prices({}, daily, a["price"], intent.style, db, consult.stop_factor)
            if short:  # mirror: the stop sits above the entry, the target below
                stop, target = round(2 * a["price"] - stop, 2), round(2 * a["price"] - target, 2)
            intent.stop_loss, intent.target = stop, target
            min_rr = float(settings_store.get(db, "trade_gate").get("min_reward_risk") or 0)
            risk, reward = abs(a["price"] - stop), abs(target - a["price"])
            if min_rr and risk > 0:
                rr = reward / risk
                intent.extra_checks.append(Check("reward_risk", rr >= min_rr,
                                                 f"target {target} / stop {stop}: reward {rr:.2f}× the risk (min {min_rr:g}×)"))
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


def is_filing(item: NewsItem) -> bool:
    u = (item.url or "").lower()
    return "nseindia.com" in u or "bseindia.com" in u or u.endswith(".pdf") or (item.source or "").startswith(("NSE", "BSE"))


def run_cycle(db: Session, md, fetch=sources.fetch_all, broker_factory=None, now: datetime | None = None,
              fetch_telegram=None, http_get=None, filing_get=None) -> dict:
    """One polling cycle: fetch, dedupe, drop stale (> max_age_minutes), read images, triage, analyse, gate."""
    now = now or utcnow()
    cfg = settings_store.get(db, "news")
    raw = list(fetch(cfg["feeds"]))
    if fetch_telegram is None and fetch is sources.fetch_all:
        fetch_telegram = sources.fetch_telegram_all
    if fetch_telegram and cfg.get("telegram_channels"):
        raw += fetch_telegram(cfg["telegram_channels"])
    new = ingest(db, raw, now=now)
    _save_health(db)
    fresh = now - max_age(db)
    held = [t.stock for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN))]
    extra = list(dict.fromkeys(cfg["watchlist"] + held))
    stats = {"fetched": len(new), "duplicates": sum(i.status == "DUPLICATE" for i in new), "stale": 0, "images_read": 0,
             "triaged": 0, "relevant": 0, "signals": 0, "symbol_duplicates": 0, "errors": 0}

    # anything that went past max_age while waiting is skipped: no AI call, no trade
    for item in db.scalars(select(NewsItem).where(NewsItem.status.in_(("NEW", "ERROR", "TRIAGED")), NewsItem.published_at < fresh)):
        item.status = "STALE"
        stats["stale"] += 1
    db.commit()
    stats["stale"] += sum(i.status == "STALE" for i in new)

    pending = fair_queue(list(db.scalars(select(NewsItem).where(NewsItem.status == "NEW"))), int(cfg["max_items_per_run"]))
    # one more try for items whose triage failed (e.g. a garbled AI answer), while the news is still fresh
    pending += list(db.scalars(select(NewsItem).where(NewsItem.status == "ERROR").order_by(NewsItem.published_at).limit(10)))
    by_symbol: dict[str, tuple[list[NewsItem], str | None]] = {}
    retry_ids: set[int] = set()
    # relevant items whose analysis failed earlier (e.g. prices were unavailable) are analysed again while fresh
    for item in db.scalars(select(NewsItem).where(NewsItem.status == "TRIAGED").limit(20)):
        tri = db.scalar(select(NewsAnalysis).where(NewsAnalysis.news_item_id == item.id, NewsAnalysis.stage == "triage")
                        .order_by(NewsAnalysis.id.desc()))
        for s in (tri.symbols or []) if tri and tri.relevant else []:
            lst, _ = by_symbol.get(s, ([], None))
            by_symbol[s] = (lst + [item], tri.event_type)
            retry_ids.add(item.id)
    stats["retried"] = len(retry_ids)

    for item in pending:
        try:
            if cfg.get("read_images", True) and item.image_urls and "[Text read from image]" not in (item.body or ""):
                stats["images_read"] += read_images(db, item, http_get)
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
            db.rollback()
            item.status = "FAILED" if item.status == "ERROR" else "ERROR"  # FAILED = gave up after the retry
            db.add(NewsAnalysis(news_item_id=item.id, stage="triage", relevant=False, symbols=[],
                                summary=f"Error: {friendly_error(e)}", result={"error": str(e)[:500]}))
            db.commit()
            stats["errors"] += 1
            log.error("triage failed", extra={"news_id": item.id, "error": str(e)})

    window = now - timedelta(minutes=float(cfg.get("symbol_dedup_minutes") or 30))
    for sym, (items, etype) in by_symbol.items():
        # the same stock + same kind of event was already analysed recently (another source's version of the story)
        recent = [a for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.stage == "analysis", NewsAnalysis.created_at >= window))
                  if sym in (a.symbols or []) and (a.event_type or None) == (etype or None)
                  and a.news_item_id not in {i.id for i in items}]
        if recent:
            for it in items:
                it.status, it.dup_of_id = "DUPLICATE", recent[-1].news_item_id
            db.commit()
            stats["symbol_duplicates"] += 1
            continue
        for it in items:  # exchange filings: read the attached PDF so the AI sees the numbers, not just the headline
            if is_filing(it) and sources.FILING_MARK not in (it.body or ""):
                try:
                    txt = (filing_get or sources.filing_text)(it.url)
                    if txt:
                        it.body = f"{(it.body or '').strip()}\n\n{sources.FILING_MARK}\n{txt}".strip()
                        stats["filings_read"] = stats.get("filings_read", 0) + 1
                except Exception as e:
                    log.info("filing pdf not read", extra={"news_id": it.id, "error": str(e)[:200]})
        db.commit()
        try:
            process_symbol(db, sym, items, md, etype, broker_factory, now=now)
            stats["signals"] += 1
        except (llm.BudgetExceeded, llm.LLMUnavailable) as e:
            log.warning("news analysis paused", extra={"reason": str(e)})
            break
        except Exception as e:
            db.rollback()
            for it in items:
                if it.id in retry_ids:
                    it.status = "FAILED"  # already retried once: stop spending AI calls on it
            db.commit()
            stats["errors"] += 1
            log.error("analysis failed", extra={"stock": sym, "error": str(e)})
            reject_error(db, sym, items, "analysis", e)
    if any(v for k, v in stats.items()):
        events.publish(db, "news_cycle", stats)
    return stats
