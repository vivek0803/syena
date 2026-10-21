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
    style: str  # INTRADAY | SWING | LONG_TERM
    entry_type: str  # market | range
    entry_low: float | None
    entry_high: float | None
    entry_reason: str
    pe: float | None
    exit_condition: str
    target_pct: float | None  # % above entry
    stop_loss_pct: float | None  # % below entry
    holding_days: int


def entry_ref(plan: InvestPlan, ltp: float | None) -> float | None:
    """Price the percentages are measured from: your range top for range entries, else the live price."""
    if plan.entry_type == "range" and plan.entry_high:
        return plan.entry_high if ltp is None else min(ltp, plan.entry_high)
    return ltp


def levels(plan: InvestPlan, ref: float | None) -> tuple[float | None, float | None]:
    """(target price, stop-loss price) from the percentages, rounded to the 0.05 tick."""
    if not ref:
        return None, None
    tick = lambda x: round(round(x / 0.05) * 0.05, 2)  # noqa: E731
    target = tick(ref * (1 + plan.target_pct / 100)) if plan.target_pct else None
    stop = tick(ref * (1 - plan.stop_loss_pct / 100)) if plan.stop_loss_pct else None
    return target, stop


def deterministic_issues(plan: InvestPlan, ltp: float | None, sizing: dict) -> list[dict]:
    issues = []
    ref = ltp or plan.entry_high or plan.entry_low
    if plan.entry_type == "range":
        if not plan.entry_low or not plan.entry_high or plan.entry_low > plan.entry_high:
            issues.append({"field": "entry_range", "problem": "entry range low must be ≤ high", "severity": "high"})
        elif ltp and not plan.entry_low <= ltp <= plan.entry_high:
            issues.append({"field": "entry_range", "problem": f"current price {ltp} is outside your range {plan.entry_low}-{plan.entry_high}",
                           "severity": "medium"})
    if plan.style in ("INTRADAY", "SWING"):
        if not plan.target_pct:
            issues.append({"field": "target_pct", "problem": "set a target % for intraday/swing trades", "severity": "high"})
        if not plan.stop_loss_pct:
            issues.append({"field": "stop_loss_pct", "problem": "set a stop-loss % for intraday/swing trades", "severity": "high"})
    if plan.target_pct and plan.stop_loss_pct:
        rr = plan.target_pct / plan.stop_loss_pct
        if rr < 1:
            issues.append({"field": "target_pct", "problem": f"reward:risk is {rr:.2f} (target {plan.target_pct}% vs stop {plan.stop_loss_pct}%)",
                           "severity": "medium"})
    if plan.style == "INTRADAY" and plan.target_pct and plan.target_pct > 8:
        issues.append({"field": "target_pct", "problem": f"a {plan.target_pct}% move in one day is rare", "severity": "medium"})
    if plan.style == "LONG_TERM" and plan.stop_loss_pct and plan.stop_loss_pct < 8:
        issues.append({"field": "stop_loss_pct", "problem": f"a {plan.stop_loss_pct}% stop is tight for a long-term holding; normal swings may trigger it",
                       "severity": "low"})
    if ref:
        sz = size_position(ref, sizing["min_amount"], sizing["max_amount"])
        if not sz.ok:
            issues.append({"field": "symbol", "problem": f"position sizing: {sz.reason}", "severity": "high"})
    if plan.style == "LONG_TERM" and 0 < plan.holding_days < 90:
        issues.append({"field": "holding_days", "problem": f"{plan.holding_days} days is short for long-term; consider Swing",
                       "severity": "low"})
    if plan.style == "INTRADAY" and plan.holding_days > 0:
        issues.append({"field": "holding_period", "problem": "intraday trades are squared off the same day", "severity": "medium"})
    if plan.pe is not None and (plan.pe <= 0 or plan.pe > 300):
        issues.append({"field": "pe", "problem": f"P/E {plan.pe} looks implausible: please double-check", "severity": "low"})
    return issues


def review(db: Session, plan: InvestPlan, md) -> dict:
    sym = plan.symbol.upper().strip()
    now = utcnow()
    if not sym:
        raise ValueError("Enter a stock symbol, e.g. RELIANCE.")
    try:
        ltp = md.ltp([sym]).get(sym)
    except Exception as e:
        if type(e).__name__ in ("InputException", "DataException"):
            ltp = None
        else:
            raise
    if ltp is None:
        raise ValueError(f"No price found for NSE:{sym}. Check the symbol (e.g. RELIANCE, TCS, INFY).")
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
    ref = entry_ref(plan, ltp)
    target_px, stop_px = levels(plan, ref)
    ai = {"verdict": None, "issues": [], "suggested": {}, "summary": "AI review unavailable (no API key configured)."}
    ai_call_id = model = None
    if llm.available():
        try:
            res = llm.call(db, "manual_review", "manual_review", tier="analysis", max_tokens=900, variables={
                "plan": untrusted(json.dumps({**asdict(plan), "entry_price": ref, "target_price": target_px,
                                              "stop_loss_price": stop_px}), "user_plan"),
                "fundamentals": json.dumps({"pe_from_you": plan.pe, "note": "no independent fundamentals feed is connected; "
                                            "treat P/E as user-supplied and unverified", "ltp": ltp}),
                "news": untrusted(json.dumps(news[:10]), "news"), "technicals": json.dumps(tech),
                "lessons": pretrade.lessons_context(active)})
            ai, ai_call_id, model = res.data or ai, res.ai_call_id, res.model
        except Exception as e:
            ai["summary"] = f"AI review failed: {e}"
    suggested = dict(ai.get("suggested") or {})
    for k_abs, k_pct, sign in (("target", "target_pct", 1), ("stop_loss", "stop_loss_pct", -1)):
        v = suggested.pop(k_abs, None)
        if v is not None and ref and k_pct not in suggested:
            try:
                suggested[k_pct] = round(sign * (float(v) / ref - 1) * 100, 1)
            except (TypeError, ValueError):
                pass
    verdict = ai.get("verdict") or ("avoid" if any(i["severity"] == "high" for i in issues) else "caution" if issues else "proceed")
    sig = Signal(source="manual", symbol=sym, action="BUY", confidence=0, reasoning=ai.get("summary"),
                 indicators=tech, model=model, prompt_version="v2" if model else None, price_at_signal=ltp, ts=now,
                 cited=[n["id"] for n in news[:10]])
    db.add(sig)
    db.commit()
    return {"review_id": sig.id, "symbol": sym, "ltp": ltp, "verdict": verdict, "issues": issues + (ai.get("issues") or []),
            "suggested": suggested, "levels": {"entry": ref, "target": target_px, "stop_loss": stop_px}, "summary": ai.get("summary"), "technicals": tech, "news": news[:10],
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
    target_px, stop_px = levels(plan, limit or ltp)
    intent = engine.TradeIntent(
        symbol=sym, style=Style(plan.style), source="manual", ref_price=ltp or 0.0, signal_id=sig.id,
        stop_loss=stop_px, target=target_px, exit_condition=plan.exit_condition, max_hold_days=plan.holding_days or None,
        auto_sell=False, idempotency_key=f"manual-sig{sig.id}", extra_checks=checks + consult.checks,
        reasoning={"entry_reason": plan.entry_reason, "ai_review": sig.reasoning, "pe": plan.pe},
        inputs={"plan": asdict(plan), "ltp": ltp, "target_price": target_px, "stop_loss_price": stop_px}, lessons_consulted=consult.consulted, lessons_applied=consult.applied,
        lessons_would_apply=consult.would_apply, similar_trades=consult.similar, setup=setup,
        limit_price=limit, order_type="LIMIT",
    )
    d = engine.enter(db, intent, broker_factory, now=now)
    pretrade.record(db, d.id, consult)
    return d
