"""Pre-trade lesson check (spec 18D): retrieve matching lessons, apply ACTIVE ones, shadow-log TESTING ones,
and fetch the most similar past trades for the analysis step."""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.ai.embeddings import cosine, embed
from syena.ai.prompts import untrusted
from syena.execution.checks import Check
from syena.learning.features import describe_setup
from syena.learning.lessons import matches
from syena.models import Lesson, LessonApplication, Trade, TradeReview, TradeStatus


@dataclass
class Consultation:
    consulted: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    would_apply: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    confidence: float | None = None
    stop_factor: float = 1.0
    similar: list = field(default_factory=list)


def retrieve(db: Session, features: dict, statuses=("ACTIVE", "TESTING"), limit: int = 12, as_of=None) -> list[Lesson]:
    q = embed(describe_setup(features))
    cfg = settings_store.get(db, "lessons")
    scored = []
    stmt = select(Lesson).where(Lesson.status.in_(statuses))
    if as_of is not None:  # replay: only lessons that existed (and were approved) at that time
        stmt = stmt.where(Lesson.created_at <= as_of)
    for l in db.scalars(stmt):
        if as_of is not None and l.status == "ACTIVE" and (l.approved_at is None or l.approved_at > as_of):
            continue
        if matches(l.trigger, features):
            scored.append((1.0 + cosine(q, l.embedding), l))
    scored.sort(key=lambda x: (-x[1].support_count, -x[0]))
    budget, out, used = cfg["context_token_budget"], [], 0
    for _, l in scored[:limit]:
        cost = len(l.text) // 4 + 20
        if used + cost > budget:
            break
        out.append(l)
        used += cost
    return out


def lessons_context(lessons: list[Lesson]) -> str:
    if not lessons:
        return "(none)"
    rows = [f"{l.code} [{l.status}] support={l.support_count} contradict={l.contradiction_count} "
            f"action={l.action['type']}{'' if l.action.get('value') is None else ' ' + str(l.action['value'])}: {l.text}"
            for l in lessons if l.status == "ACTIVE"]
    return untrusted("\n".join(rows) or "(none)", "lessons")


def similar_trades(db: Session, features: dict, k: int = 5, as_of=None) -> list[dict]:
    q = embed(describe_setup(features))
    cands = []
    stmt = select(Trade).where(Trade.status == TradeStatus.CLOSED)
    if as_of is not None:  # replay: only trades already closed at that time
        stmt = stmt.where(Trade.closed_at <= as_of)
    for t in db.scalars(stmt.order_by(Trade.id.desc()).limit(500)):
        if t.setup:
            cands.append((cosine(q, embed(describe_setup(t.setup))), t))
    cands.sort(key=lambda x: -x[0])
    out = []
    for score, t in cands[:k]:
        rev = db.scalar(select(TradeReview).where(TradeReview.trade_id == t.id))
        out.append({"trade_id": t.id, "stock": t.stock, "similarity": round(score, 3), "pnl": t.profit_loss,
                    "exit_reason": t.exit_reason, "classification": rev.classification if rev else None,
                    "review": rev.summary if rev else None})
    return out


def _apply(l: Lesson, conf: float | None, features: dict, threshold: float) -> tuple[dict, Check | None, float | None, float]:
    t, v = l.action["type"], l.action.get("value")
    stop_factor = 1.0
    effect: dict = {"lesson": l.code, "action": t}
    check = None
    if t == "avoid":
        effect["detail"] = f"blocks entry ({l.text[:80]})"
        check = Check(f"lesson:{l.code}", False, f"REJECTED by lesson {l.code}: {l.text[:120]}")
    elif t in ("reduce_confidence", "prefer") and conf is not None:
        new = max(0.0, min(100.0, conf - v if t == "reduce_confidence" else conf + v))
        effect["detail"] = f"confidence {conf:g} -> {new:g}"
        if new < threshold <= conf:
            effect["detail"] += f", below threshold {threshold:g}"
            check = Check(f"lesson:{l.code}", False, f"confidence {conf:g} -> {new:g}, below threshold, REJECTED by lesson {l.code}")
        conf = new
    elif t == "require_confirmation":
        ok = bool(features.get("technicals_agree"))
        effect["detail"] = "extra confirmation " + ("present" if ok else "missing")
        check = Check(f"lesson:{l.code}", ok, f"lesson {l.code} requires technical confirmation: {'ok' if ok else 'missing'}")
    elif t == "tighten_stop":
        stop_factor = v
        effect["detail"] = f"stop distance × {v:g}"
    else:
        effect["detail"] = "no effect for this signal type"
    effect["changed_outcome"] = bool(check and not check.passed)
    return effect, check, conf, stop_factor


def consult(db: Session, features: dict, confidence: float | None, with_similar: bool = True, as_of=None) -> Consultation:
    threshold = settings_store.get(db, "trade_gate")["confidence_threshold"]
    res = Consultation(confidence=confidence)
    lessons = retrieve(db, features, as_of=as_of)
    for l in lessons:
        res.consulted.append({"code": l.code, "id": l.id, "status": l.status, "text": l.text,
                              "support": l.support_count, "contradiction": l.contradiction_count})
        if l.status == "ACTIVE":
            effect, check, res.confidence, sf = _apply(l, res.confidence, features, threshold)
            res.stop_factor = min(res.stop_factor, sf)
            res.applied.append(effect)
            if check:
                res.checks.append(check)
        else:  # TESTING: shadow only
            effect, check, _, _ = _apply(l, res.confidence, features, threshold)
            effect["would_have"] = effect.pop("detail")
            res.would_apply.append(effect)
    if with_similar:
        res.similar = similar_trades(db, features, as_of=as_of)
    return res


def record(db: Session, decision_id: int, c: Consultation) -> None:
    for e in c.applied:
        lid = next((x["id"] for x in c.consulted if x["code"] == e["lesson"]), None)
        if lid:
            db.add(LessonApplication(lesson_id=lid, decision_id=decision_id, status_at_time="ACTIVE", applied=True,
                                     changed_outcome=e.get("changed_outcome", False), effect=e))
    for e in c.would_apply:
        lid = next((x["id"] for x in c.consulted if x["code"] == e["lesson"]), None)
        if lid:
            db.add(LessonApplication(lesson_id=lid, decision_id=decision_id, status_at_time="TESTING", applied=False,
                                     changed_outcome=e.get("changed_outcome", False), effect=e))
    db.commit()
