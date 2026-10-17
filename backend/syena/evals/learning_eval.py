"""Evals for the learning loop (18F): do lesson-influenced decisions beat comparable ones without lessons,
how precise is each lesson, how many were retired, holdout comparison, and reviewer-vs-your-labels agreement."""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.evals import metrics as M
from syena.evals import runner
from syena.learning.lessons import is_holdout
from syena.models import Decision, Lesson, LessonApplication, ManualLabel, Trade, TradeReview, TradeStatus


def _stats(trades: list[Trade]) -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if (t.profit_loss or 0) > 0)
    return {"n": n, "win_rate": round(wins / n, 4) if n else None, "ci95": M.wilson(wins, n),
            "avg_pnl": round(sum(t.profit_loss or 0 for t in trades) / n, 2) if n else None}


def compute(db: Session) -> dict:
    closed = list(db.scalars(select(Trade).where(Trade.status == TradeStatus.CLOSED)))
    apps = list(db.scalars(select(LessonApplication)))
    influenced_decisions = {a.decision_id for a in apps if a.applied}
    influenced = [t for t in closed if t.decision_id in influenced_decisions]
    without = [t for t in closed if t.decision_id not in influenced_decisions]
    # precision of shadow (TESTING) and active lessons where an outcome exists
    per_lesson = defaultdict(lambda: {"right": 0, "wrong": 0})
    for a in apps:
        if a.was_right is None:
            continue
        code = db.get(Lesson, a.lesson_id).code
        per_lesson[code]["right" if a.was_right else "wrong"] += 1
    precision = {k: {**v, "precision": round(v["right"] / (v["right"] + v["wrong"]), 3)} for k, v in per_lesson.items()}
    # reviewer vs manual labels
    agree, total, hindsight = 0, 0, 0
    reviews = list(db.scalars(select(TradeReview).where(TradeReview.kind == "closed_trade")))
    for r in reviews:
        hindsight += 1 if r.hindsight_flags else 0
        lab = db.scalar(select(ManualLabel).where(ManualLabel.kind == "review", ManualLabel.ref_id == r.id))
        if lab:
            total += 1
            agree += lab.label.get("classification") == r.classification
    lessons = list(db.scalars(select(Lesson)))
    return {
        "with_lessons": _stats(influenced), "without_lessons": _stats(without),
        "holdout": _stats([t for t in closed if is_holdout(t)]), "non_holdout": _stats([t for t in closed if not is_holdout(t)]),
        "lesson_precision": precision,
        "lessons_by_status": {s: sum(1 for l in lessons if l.status == s) for s in ("PROPOSED", "TESTING", "ACTIVE", "RETIRED")},
        "reviewer_agreement_with_you": {"n": total, "agreement": round(agree / total, 3) if total else None},
        "reviews_with_hindsight_flags": {"n": len(reviews), "flagged": hindsight},
        "shadow_would_change": sum(1 for a in apps if not a.applied and a.changed_outcome),
        "decisions_total": db.query(Decision).count(),
    }


def run_suite(db: Session, config: dict | None = None, triggered_by: str = "manual"):
    run = runner.start(db, "learning", config or {}, triggered_by)
    try:
        m = compute(db)
        w, wo = m["with_lessons"], m["without_lessons"]
        passed = None if (w["n"] or 0) < 10 or (wo["n"] or 0) < 10 else (w["avg_pnl"] or 0) >= (wo["avg_pnl"] or 0)
        m["status"] = "insufficient data" if passed is None else "ok"
        return runner.finish(db, run, m, passed, (w["n"] or 0) + (wo["n"] or 0))
    except Exception as e:
        return runner.fail(db, run, e)
