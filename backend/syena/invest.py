"""Assisted manual invest (spec 8): you propose, the AI reviews, nothing is ordered until you confirm."""
import json
import logging
from dataclasses import asdict, dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.ai import llm
from syena.ai.prompts import untrusted
from syena.db import utcnow
from syena.execution import engine
from syena.execution.checks import Check
from syena.execution.sizing import size_position
from syena.learning import pretrade
from syena.learning.features import build_setup
from syena.models import Decision, NewsAnalysis, NewsItem, Signal, Style
from syena.news.context import technicals

log = logging.getLogger("syena.invest")


@dataclass
class InvestPlan:
    symbol: str
    style: str  # INTRADAY | SWING
    entry_type: str  # market | range
    entry_low: float | None
    entry_high: float | None
    entry_reason: str
    pe: float | None
    exit_condition: str
    target: float
    stop_loss: float
    holding_days: int


def deterministic_issues(plan: InvestPlan, ltp: float | None, sizing: dict) -> list[dict]:
    issues = []
    ref = ltp or plan.entry_high or plan.entry_low
    if plan.entry_type == "range":
        if not plan.entry_low or not plan.entry_high or plan.entry_low > plan.entry_high:
            issues.append({"field": "entry_range", "problem": "entry range low must be ≤ high", "severity": "high"})
        elif ltp and not plan.entry_low <= ltp <= plan.entry_high:
            issues.append({"field": "entry_range", "problem": f"current price {ltp} is outside your range {plan.entry_low}-{plan.entry_high}",
                           "severity": "medium"})
    if ref:
        if plan.stop_loss >= ref:
            issues.append({"field": "stop_loss", "problem": f"stop {plan.stop_loss} is not below the entry ~{ref}", "severity": "high"})
        if plan.target <= ref:
            issues.append({"field": "target", "problem": f"target {plan.target} is not above the entry ~{ref}", "severity": "high"})
        if plan.stop_loss < ref and plan.target > ref:
            rr = (plan.target - ref) / (ref - plan.stop_loss)
            if rr < 1:
                issues.append({"field": "target", "problem": f"reward:risk is {rr:.2f} (< 1)", "severity": "medium"})
        sz = size_position(ref, sizing["min_amount"], sizing["max_amount"])
        if not sz.ok:
            issues.append({"field": "symbol", "problem": f"position sizing: {sz.reason}", "severity": "high"})
    if plan.style == "INTRADAY" and plan.holding_days > 0:
        issues.append({"field": "holding_period", "problem": "intraday trades are squared off the same day", "severity": "medium"})
    if plan.pe is not None and (plan.pe <= 0 or plan.pe > 300):
        issues.append({"field": "pe", "problem": f"P/E {plan.pe} looks implausible: please double-check", "severity": "low"})
    return issues


def review(db: Session, plan: InvestPlan, md) -> dict:
    sym = plan.symbol.upper().strip()
    now = utcnow()
    ltp = md.ltp([sym]).get(sym)
    daily = md.candles(sym, "day", now - timedelta(days=400), now)
    tech = technicals(daily)
    news = []
    for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.created_at >= now - timedelta(days=7)).order_by(NewsAnalysis.id.desc()).limit(300)):
        if sym in (a.symbols or []):
            item = db.get(NewsItem, a.news_item_id)
            news.append({"id": item.id, "title": item.title, "summary": a.summary, "sentiment": a.sentiment})
    setup = build_setup(symbol=sym, style=plan.style, source="manual", ts=now, df=daily)
    consult = pretrade.consult(db, setup, None)
    active = pretrade.retrieve(db, setup, statuses=("ACTIVE",))
    issues = deterministic_issues(plan, ltp, settings_store.get(db, "sizing"))
    ai = {"verdict": None, "issues": [], "suggested": {}, "summary": "AI review unavailable (no API key configured)."}
    ai_call_id = model = None
    if llm.available():
        try:
            res = llm.call(db, "manual_review", "manual_review", tier="analysis", max_tokens=900, variables={
                "plan": untrusted(json.dumps(asdict(plan)), "user_plan"),
                "fundamentals": json.dumps({"pe_from_you": plan.pe, "note": "no independent fundamentals feed is connected; "
                                            "treat P/E as user-supplied and unverified", "ltp": ltp}),
                "news": untrusted(json.dumps(news[:10]), "news"), "technicals": json.dumps(tech),
                "lessons": pretrade.lessons_context(active)})
            ai, ai_call_id, model = res.data or ai, res.ai_call_id, res.model
        except Exception as e:
            ai["summary"] = f"AI review failed: {e}"
    verdict = ai.get("verdict") or ("avoid" if any(i["severity"] == "high" for i in issues) else "caution" if issues else "proceed")
    sig = Signal(source="manual", symbol=sym, action="BUY", confidence=0, reasoning=ai.get("summary"),
                 indicators=tech, model=model, prompt_version="v1" if model else None, price_at_signal=ltp, ts=now,
                 cited=[n["id"] for n in news[:10]])
    db.add(sig)
    db.commit()
    return {"review_id": sig.id, "symbol": sym, "ltp": ltp, "verdict": verdict, "issues": issues + (ai.get("issues") or []),
            "suggested": ai.get("suggested") or {}, "summary": ai.get("summary"), "technicals": tech, "news": news[:10],
            "lessons": consult.consulted, "similar_trades": consult.similar, "ai_call_id": ai_call_id, "plan": asdict(plan)}


def confirm(db: Session, review_id: int, plan: InvestPlan, md, broker_factory=None) -> Decision:
    """Places the order (paper or live per mode). Only called after you click Yes in the UI."""
    sig = db.get(Signal, review_id)
    if sig is None or sig.source != "manual":
        raise ValueError("unknown review")
    sym = plan.symbol.upper().strip()
    ltp = md.ltp([sym]).get(sym)
    checks = [Check("you_confirmed", True, "you reviewed the AI verdict and clicked Yes")]
    limit = None
    if ltp is None:
        checks.append(Check("price", False, "no live price"))
    elif plan.entry_type == "range":
        ok = plan.entry_low <= ltp <= plan.entry_high
        checks.append(Check("entry_range", ok, f"LTP {ltp} vs your range {plan.entry_low}-{plan.entry_high}"))
        limit = plan.entry_high
    for i in deterministic_issues(plan, ltp, settings_store.get(db, "sizing")):
        if i["severity"] == "high":
            checks.append(Check(f"plan:{i['field']}", False, i["problem"]))
    now = utcnow()
    daily = md.candles(sym, "day", now - timedelta(days=200), now)
    setup = build_setup(symbol=sym, style=plan.style, source="manual", ts=now, df=daily)
    consult = pretrade.consult(db, setup, None)
    intent = engine.TradeIntent(
        symbol=sym, style=Style(plan.style), source="manual", ref_price=ltp or 0.0, signal_id=sig.id,
        stop_loss=plan.stop_loss, target=plan.target, exit_condition=plan.exit_condition, max_hold_days=plan.holding_days or None,
        auto_sell=False, idempotency_key=f"manual-sig{sig.id}", extra_checks=checks + consult.checks,
        reasoning={"entry_reason": plan.entry_reason, "ai_review": sig.reasoning, "pe": plan.pe},
        inputs={"plan": asdict(plan), "ltp": ltp}, lessons_consulted=consult.consulted, lessons_applied=consult.applied,
        lessons_would_apply=consult.would_apply, similar_trades=consult.similar, setup=setup,
        limit_price=limit, order_type="LIMIT",
    )
    d = engine.enter(db, intent, broker_factory, now=now)
    pretrade.record(db, d.id, consult)
    return d
