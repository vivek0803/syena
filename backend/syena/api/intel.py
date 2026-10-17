"""News feed, manual invest, evals, learning loop."""
import threading
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import invest as invest_svc
from syena.api.common import market_data, ser
from syena.api.deps import authenticated
from syena.db import SessionLocal, get_db, utcnow
from syena.evals import online
from syena.evals import runner as eval_runner
from syena.execution import engine
from syena.learning import lessons as L
from syena.learning import mining
from syena.market.calendar import to_ist
from syena.models import (Decision, EvalResult, EvalRun, Lesson, LessonApplication, LessonEvidence, ManualLabel, NewsAnalysis,
                          NewsItem, Trade, TradeReview, TradeStatus)
from syena.news import pipeline

router = APIRouter(prefix="/api", tags=["intel"], dependencies=[Depends(authenticated)])


# ---------------------------------------------------------------- news
@router.get("/news")
def news(status: str | None = None, symbol: str | None = None, limit: int = 100, before_id: int | None = None,
         db: Session = Depends(get_db)):
    q = select(NewsItem)
    if status:
        q = q.where(NewsItem.status == status.upper())
    if before_id:
        q = q.where(NewsItem.id < before_id)
    items = list(db.scalars(q.order_by(NewsItem.published_at.desc()).limit(min(limit, 300))))
    ids = [i.id for i in items]
    analyses = defaultdict(list)
    for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.news_item_id.in_(ids))):
        analyses[a.news_item_id].append(a)
    trades_by_news = defaultdict(list)
    for t in db.scalars(select(Trade).where(Trade.news_item_ids.is_not(None)).order_by(Trade.id.desc()).limit(500)):
        for nid in t.news_item_ids or []:
            trades_by_news[nid].append({"trade_id": t.id, "stock": t.stock, "mode": t.mode, "status": t.status.value, "pnl": t.profit_loss})
    out = []
    for i in items:
        an = analyses.get(i.id, [])
        tri = next((a for a in an if a.stage == "triage"), None)
        ana = [a for a in an if a.stage == "analysis"]
        syms = sorted({s for a in an for s in (a.symbols or [])})
        if symbol and symbol.upper() not in syms:
            continue
        out.append(ser(i, exclude=("content_hash",), extra={
            "symbols": syms, "event_type": tri.event_type if tri else None, "triage_reason": tri.summary if tri else None,
            "analyses": [{"symbol": (a.symbols or [None])[0], "sentiment": a.sentiment, "summary": a.summary,
                          "action": (a.result or {}).get("action"), "confidence": (a.result or {}).get("confidence"),
                          "signal_id": (a.result or {}).get("signal_id"), "model": a.model} for a in ana],
            "trades": trades_by_news.get(i.id, [])}))
    return out


@router.post("/news/run")
def news_run(md=Depends(market_data), db: Session = Depends(get_db)):
    return pipeline.run_cycle(db, md, broker_factory=engine.default_broker_factory(md))


class LabelIn(BaseModel):
    kind: str = Field(pattern="^(triage|mapping|judge|review)$")
    ref_id: int
    label: dict


@router.post("/labels")
def label(body: LabelIn, db: Session = Depends(get_db)):
    row = db.scalar(select(ManualLabel).where(ManualLabel.kind == body.kind, ManualLabel.ref_id == body.ref_id))
    if row:
        row.label = body.label
    else:
        db.add(ManualLabel(kind=body.kind, ref_id=body.ref_id, label=body.label))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- manual invest
class PlanIn(BaseModel):
    symbol: str = Field(max_length=32)
    style: str = Field(pattern="^(INTRADAY|SWING)$")
    entry_type: str = Field(pattern="^(market|range)$")
    entry_low: float | None = None
    entry_high: float | None = None
    entry_reason: str = Field(max_length=2000)
    pe: float | None = None
    exit_condition: str = Field(default="", max_length=1000)
    target: float
    stop_loss: float
    holding_days: int = Field(default=0, ge=0, le=3650)


@router.post("/invest/review")
def invest_review(plan: PlanIn, md=Depends(market_data), db: Session = Depends(get_db)):
    return invest_svc.review(db, invest_svc.InvestPlan(**plan.model_dump()), md)


class ConfirmIn(BaseModel):
    review_id: int
    plan: PlanIn


@router.post("/invest/confirm")
def invest_confirm(body: ConfirmIn, md=Depends(market_data), db: Session = Depends(get_db)):
    d = invest_svc.confirm(db, body.review_id, invest_svc.InvestPlan(**body.plan.model_dump()), md,
                           engine.default_broker_factory(md))
    return ser(d)


# ---------------------------------------------------------------- evals
@router.get("/evals")
def evals_summary(db: Session = Depends(get_db)):
    out = {}
    for suite in eval_runner.suites():
        runs = list(db.scalars(select(EvalRun).where(EvalRun.suite == suite).order_by(EvalRun.id.desc()).limit(20)))
        out[suite] = {"latest": ser(runs[0]) if runs else None,
                      "trend": [{"id": r.id, "at": r.started_at.isoformat(), "passed": r.passed, "metrics": r.metrics,
                                 "config": r.config} for r in reversed(runs)]}
    return out


@router.get("/evals/runs/{run_id}")
def eval_run(run_id: int, only_failed: bool = False, db: Session = Depends(get_db)):
    r = db.get(EvalRun, run_id)
    if r is None:
        raise HTTPException(404, "not found")
    q = select(EvalResult).where(EvalResult.run_id == run_id)
    if only_failed:
        q = q.where(EvalResult.passed.is_(False))
    return {"run": ser(r), "results": [ser(x) for x in db.scalars(q.limit(1000))]}


class RunIn(BaseModel):
    suite: str
    config: dict = Field(default_factory=dict)


def _bg(fn, *args):
    def target():
        with SessionLocal() as db:
            fn(db, *args)
    threading.Thread(target=target, daemon=True).start()


@router.post("/evals/run")
def eval_start(body: RunIn):
    suites = eval_runner.suites()
    if body.suite not in suites:
        raise HTTPException(400, f"unknown suite; one of {list(suites)}")
    _bg(lambda db: suites[body.suite](db, body.config, "manual"))
    return {"started": body.suite}


class CompareIn(BaseModel):
    suite: str
    configs: list[dict] = Field(min_length=2, max_length=4)


@router.post("/evals/compare")
def eval_compare(body: CompareIn):
    if body.suite not in eval_runner.suites():
        raise HTTPException(400, "unknown suite")
    _bg(lambda db: eval_runner.compare(db, body.suite, body.configs))
    return {"started": body.suite, "configs": len(body.configs)}


@router.get("/evals/online")
def eval_online(db: Session = Depends(get_db)):
    from syena.models import Alert

    now = utcnow()
    return {"last_14d": online.accuracy_by(db, now - timedelta(days=14), now),
            "prior_28d": online.accuracy_by(db, now - timedelta(days=42), now - timedelta(days=14)),
            "shadow_vs_champion": online.shadow_vs_champion(db),
            "drift_alerts": [ser(a) for a in db.scalars(select(Alert).where(Alert.kind == "drift").order_by(Alert.id.desc()).limit(20))]}


# ---------------------------------------------------------------- learning
@router.get("/learning/reviews")
def reviews(limit: int = 100, db: Session = Depends(get_db)):
    out = []
    for r in db.scalars(select(TradeReview).order_by(TradeReview.id.desc()).limit(min(limit, 500))):
        t = db.get(Trade, r.trade_id) if r.trade_id else None
        d = db.get(Decision, r.decision_id) if r.decision_id else None
        out.append(ser(r, extra={"stock": t.stock if t else (d.symbol if d else None), "pnl": t.profit_loss if t else None,
                                 "mode": t.mode if t else (d.mode if d else None)}))
    return out


@router.get("/learning/lessons")
def lessons(status: str | None = None, db: Session = Depends(get_db)):
    q = select(Lesson)
    if status:
        q = q.where(Lesson.status == status.upper())
    out = []
    for l in db.scalars(q.order_by(Lesson.id.desc())):
        ev = list(db.scalars(select(LessonEvidence).where(LessonEvidence.lesson_id == l.id).order_by(LessonEvidence.id.desc()).limit(50)))
        apps = db.scalar(select(func.count()).select_from(LessonApplication).where(LessonApplication.lesson_id == l.id)) or 0
        out.append(ser(l, exclude=("embedding",), extra={
            "evidence": [ser(e) for e in ev], "applications": apps,
            "distinct_days": L.distinct_days(db, l)}))
    return out


def _lesson(db, lid) -> Lesson:
    l = db.get(Lesson, lid)
    if l is None:
        raise HTTPException(404, "lesson not found")
    return l


@router.post("/learning/lessons/{lid}/validate")
def validate(lid: int, db: Session = Depends(get_db)):
    return L.validate_out_of_sample(db, _lesson(db, lid))


@router.post("/learning/lessons/{lid}/approve")
def approve(lid: int, db: Session = Depends(get_db)):
    try:
        return ser(L.approve(db, _lesson(db, lid)), exclude=("embedding",))
    except L.LessonInvalid as e:
        raise HTTPException(400, str(e))


class RetireIn(BaseModel):
    reason: str = Field(default="rejected by you", max_length=500)


@router.post("/learning/lessons/{lid}/reject")
def reject(lid: int, body: RetireIn, db: Session = Depends(get_db)):
    return ser(L.retire(db, _lesson(db, lid), f"rejected: {body.reason}"), exclude=("embedding",))


@router.post("/learning/lessons/{lid}/retire")
def retire(lid: int, body: RetireIn, db: Session = Depends(get_db)):
    return ser(L.retire(db, _lesson(db, lid), body.reason), exclude=("embedding",))


@router.post("/learning/mine")
def mine_now(db: Session = Depends(get_db)):
    pats = mining.mine(db)
    return {"patterns": [{k: v for k, v in p.items() if k != "trade_ids"} for p in pats],
            "proposed": mining.propose_from_patterns(db, pats)}


@router.get("/learning/charts")
def learning_charts(db: Session = Depends(get_db)):
    weeks = defaultdict(lambda: defaultdict(int))
    for r in db.scalars(select(TradeReview)):
        wk = to_ist(r.created_at).date()
        wk = (wk - timedelta(days=wk.weekday())).isoformat()
        weeks[wk][r.classification] += 1
    influenced = {a.decision_id for a in db.scalars(select(LessonApplication).where(LessonApplication.applied.is_(True)))}
    closed = list(db.scalars(select(Trade).where(Trade.status == TradeStatus.CLOSED)))

    def stats(ts):
        return {"n": len(ts), "win_rate": round(sum(1 for t in ts if (t.profit_loss or 0) > 0) / len(ts), 3) if ts else None,
                "pnl": round(sum(t.profit_loss or 0 for t in ts), 2)}

    impact = []
    for l in db.scalars(select(Lesson).where(Lesson.status.in_(("ACTIVE", "RETIRED", "TESTING")))):
        if l.validation:
            impact.append({"code": l.code, "status": l.status, "total_pnl_delta": l.validation.get("total_pnl_delta"),
                           "n": l.validation.get("n")})
    return {"mistakes_by_week": [{"week": k, **v} for k, v in sorted(weeks.items())],
            "with_lessons": stats([t for t in closed if t.decision_id in influenced]),
            "without_lessons": stats([t for t in closed if t.decision_id not in influenced]),
            "lesson_impact": impact}
