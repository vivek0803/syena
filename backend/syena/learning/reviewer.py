"""Post-trade reviewer (spec 18A).

Closed trades: an LLM (review tier, separate prompt from the trader) classifies decision quality vs luck,
with deterministic counterfactuals computed from the actual price path. Rejected/skipped signals are reviewed
deterministically once their 1d/5d outcome is known (missed opportunity vs correctly avoided).
Reviews can propose lessons; proposals start PROPOSED (no effect) and gather evidence.
"""
import json
import logging
from datetime import timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.ai import llm
from syena.ai.prompts import untrusted
from syena.db import utcnow
from syena.learning import lessons as L
from syena.market.calendar import to_ist
from syena.market.charges import round_trip
from syena.models import Decision, DecisionType, Lesson, NewsItem, Signal, Trade, TradeReview, TradeStatus

log = logging.getLogger("syena.reviewer")

CLASSES = {"good-entry/good-exit", "good-entry/bad-exit", "bad-entry", "bad-luck", "rule-failure", "news-misread",
           "stop-too-tight", "target-too-greedy", "correctly-avoided", "missed-opportunity"}


def counterfactuals(trade: Trade, path: pd.DataFrame) -> dict:
    """What a wider/tighter stop, a different target, or skipping would have done (deterministic)."""
    if path.empty or not trade.entry_price:
        return {"note": "no price path available"}
    entry, qty = trade.entry_price, trade.qty
    intraday = trade.style.value == "INTRADAY"
    short = trade.side == "SELL"
    if short:  # mirror prices around the entry so a short is simulated exactly like a long
        path = pd.DataFrame({"open": 2 * entry - path["open"], "high": 2 * entry - path["low"],
                             "low": 2 * entry - path["high"], "close": 2 * entry - path["close"]}, index=path.index)
    stop_d = abs(entry - trade.stop_loss) if trade.stop_loss else entry * 0.02
    tgt_d = abs(trade.target - entry) if trade.target else entry * 0.04

    def sim(stop_mult, tgt_mult):
        stop, tgt = entry - stop_d * stop_mult, entry + tgt_d * tgt_mult
        for _, r in path.iterrows():
            if r["low"] <= stop:
                px, why = min(r["open"], stop), "stop"
                break
            if r["high"] >= tgt:
                px, why = max(r["open"], tgt), "target"
                break
        else:
            px, why = float(path["close"].iloc[-1]), "end_of_window"
        pnl = (px - entry) * qty - round_trip(qty, entry, px, intraday)
        real = 2 * entry - px if short else px
        return {"pnl": round(pnl, 2), "exit": round(float(real), 2), "by": why}

    return {"actual_pnl": trade.profit_loss, "wider_stop_1.5x": sim(1.5, 1), "tighter_stop_0.5x": sim(0.5, 1),
            "target_0.5x": sim(1, 0.5), "target_1.5x": sim(1, 1.5), "skip": {"pnl": 0.0},
            "max_favourable_pct": round(float(path["high"].max() / entry * 100 - 100), 3),
            "max_adverse_pct": round(float(path["low"].min() / entry * 100 - 100), 3), "side": trade.side}


def _record(db: Session, trade: Trade) -> dict:
    d = db.get(Decision, trade.decision_id) if trade.decision_id else None
    sig = db.get(Signal, trade.signal_id) if trade.signal_id else None
    news = []
    for nid in trade.news_item_ids or []:
        n = db.get(NewsItem, nid)
        if n and n.published_at <= trade.created_at:  # only what existed at entry
            news.append({"id": n.id, "title": n.title, "published_at": n.published_at.isoformat()})
    return {
        "at_entry": {
            "ts": trade.created_at.isoformat(), "stock": trade.stock, "style": trade.style.value, "source": trade.source,
            "algo_condition": trade.algo_condition, "entry_price": trade.entry_price, "stop_loss": trade.stop_loss,
            "target": trade.target, "confidence": d.confidence if d else None,
            "reasoning": untrusted(json.dumps(d.reason_json if d else {}, default=str)[:3000], "decision"),
            "checks": d.checks_json if d else None, "signal": {"action": sig.action, "confidence": sig.confidence,
                                                              "indicators": sig.indicators, "market_context": sig.market_context} if sig else None,
            "news": untrusted(json.dumps(news)[:3000], "news"), "setup": trade.setup,
            "lessons_applied": d.lessons_applied if d else None,
        },
        "outcome": {"exit_price": trade.exit_price_executed, "exit_reason": trade.exit_reason, "charges": trade.charges,
                    "pnl": trade.profit_loss, "closed_at": trade.closed_at.isoformat() if trade.closed_at else None},
    }


def price_path(md, trade: Trade) -> pd.DataFrame:
    if md is None:
        return pd.DataFrame()
    interval = "15minute" if trade.style.value == "INTRADAY" else "day"
    end = (trade.closed_at or utcnow()) + timedelta(days=8)
    try:
        return md.candles(trade.stock, interval, trade.created_at, min(end, utcnow()))
    except Exception as e:
        log.warning("price path unavailable", extra={"stock": trade.stock, "error": str(e)})
        return pd.DataFrame()


def fallback_review(trade: Trade, cf: dict) -> dict:
    """Used when no LLM is configured: rule-based classification so the loop still works."""
    pnl = trade.profit_loss or 0
    if pnl > 0:
        cls = "good-entry/good-exit"
    elif trade.exit_reason == "stop_loss" and cf.get("wider_stop_1.5x", {}).get("pnl", 0) > 0:
        cls = "stop-too-tight"
    elif trade.exit_reason in ("square_off", "max_holding_period") and cf.get("max_favourable_pct", 0) > 2:
        cls = "good-entry/bad-exit"
    else:
        cls = "bad-luck" if cf.get("max_favourable_pct", 0) > 1 else "bad-entry"
    return {"classification": cls, "decision_quality": "unsound" if cls == "bad-entry" else "sound",
            "right": [], "wrong": [], "counterfactual": cf, "proposed_lesson": None, "hindsight_flags": [],
            "summary": f"rule-based review (no AI configured): {cls}"}


def review_closed_trade(db: Session, trade: Trade, md=None) -> TradeReview | None:
    if trade.status != TradeStatus.CLOSED or db.scalar(select(TradeReview).where(TradeReview.trade_id == trade.id)):
        return None
    path = price_path(md, trade)
    cf = counterfactuals(trade, path)
    rec = _record(db, trade)
    ai_call_id = model = pv = None
    if llm.available():
        path_txt = path[["open", "high", "low", "close"]].round(2).to_csv() if not path.empty else "(unavailable)"
        try:
            res = llm.call(db, "trade_review", "trade_reviewer", tier="review", max_tokens=1800,
                           variables={"record": json.dumps(rec, default=str), "path": path_txt[:6000],
                                      "counterfactuals": json.dumps(cf)})
            data, ai_call_id, model, pv = res.data or {}, res.ai_call_id, res.model, res.prompt_version
        except Exception as e:
            log.error("AI review failed, using rule-based review", extra={"trade": trade.id, "error": str(e)})
            data = fallback_review(trade, cf)
    else:
        data = fallback_review(trade, cf)
    cls = data.get("classification") if data.get("classification") in CLASSES else "bad-luck"
    pnl = trade.profit_loss or 0
    review = TradeReview(
        trade_id=trade.id, decision_id=trade.decision_id, kind="closed_trade", classification=cls,
        decision_quality="sound" if data.get("decision_quality") == "sound" else "unsound",
        outcome="win" if pnl > 0 else "loss" if pnl < 0 else "flat", right=data.get("right"), wrong=data.get("wrong"),
        counterfactual=cf, proposed_lesson=data.get("proposed_lesson"), hindsight_flags=data.get("hindsight_flags"),
        summary=(data.get("summary") or "")[:1000], model=model, prompt_version=pv, ai_call_id=ai_call_id,
    )
    db.add(review)
    db.flush()
    _update_lessons(db, trade, review)
    db.commit()
    log.info("trade reviewed", extra={"trade": trade.id, "stock": trade.stock, "classification": cls})
    return review


def _update_lessons(db: Session, trade: Trade, review: TradeReview) -> None:
    day = to_ist(trade.created_at).date()
    features = trade.setup or {}
    # 1) evidence for/against existing lessons whose trigger matches this trade
    for l in db.scalars(select(Lesson).where(Lesson.status.in_(("PROPOSED", "TESTING", "ACTIVE")))):
        if L.matches(l.trigger, features):
            L.add_evidence(db, l, L.evidence_supports(l, trade, review), trade.id, review.id, day)
    # 2) a newly proposed lesson (never from news text directly: validated, starts with no effect)
    p = review.proposed_lesson
    if isinstance(p, dict) and p.get("text") and p.get("action"):
        try:
            trig = p.get("trigger") or {}
            trig.setdefault("style", trade.style.value)
            lesson = L.create_proposed(db, p["text"], trig, p["action"], source="review")
            L.add_evidence(db, lesson, True, trade.id, review.id, day, note="proposed by review")
        except L.LessonInvalid as e:
            log.info("proposed lesson rejected by validator", extra={"trade": trade.id, "error": str(e)})


def review_unexecuted(db: Session, decision: Decision, ret_1d: float | None, ret_5d: float | None) -> TradeReview | None:
    """Rejected/skipped BUY signals once the outcome is known."""
    if decision.type not in (DecisionType.REJECTED, DecisionType.SKIPPED) or ret_5d is None:
        return None
    if db.scalar(select(TradeReview).where(TradeReview.decision_id == decision.id)):
        return None
    cost_pct = 0.35  # approx round-trip delivery costs on a ₹10k position
    gained = ret_5d - cost_pct
    cls = "missed-opportunity" if gained > 1.0 else "correctly-avoided"
    failing = next((c["name"] for c in decision.checks_json or [] if not c.get("passed")), "unknown")
    review = TradeReview(
        decision_id=decision.id, kind=decision.type.value.lower(), classification=cls,
        decision_quality="sound" if cls == "correctly-avoided" else "unsound",
        outcome="missed_gain" if cls == "missed-opportunity" else "avoided_loss",
        counterfactual={"ret_1d_pct": ret_1d, "ret_5d_pct": ret_5d, "failing_check": failing},
        summary=f"{decision.symbol}: rejected by {failing}; next 5 days {ret_5d:+.2f}% -> {cls}",
    )
    db.add(review)
    db.commit()
    return review
