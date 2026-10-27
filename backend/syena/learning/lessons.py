"""Lessons memory with anti-overfitting promotion rules (spec 18B/C).

PROPOSED (no effect) -> TESTING (shadow: logged "would have applied") once supported by >= N independent
trades across >= D days -> ACTIVE only after out-of-sample validation passes AND you approve in the UI.
ACTIVE lessons are re-validated every 30 days and retired when evidence weakens. Nothing is ever deleted.
Lessons can adjust confidence, add a confirmation check, tighten stops, or block an entry. They can never
touch sizing, risk limits, the daily-loss halt or the LIVE gate.
"""
import logging
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.ai.embeddings import embed
from syena.db import utcnow
from syena.market.calendar import to_ist
from syena.models import Lesson, LessonEvidence, Trade, TradeReview, TradeStatus

log = logging.getLogger("syena.lessons")

ACTION_TYPES = {"avoid", "reduce_confidence", "tighten_stop", "require_confirmation", "prefer"}
TRIGGER_KEYS = {"symbols", "sectors", "style", "source", "event_types", "time_of_day", "vix_regime", "weekday",
                "algo_id", "ranges", "above_sma50"}
RANGE_FEATURES = {"rsi", "adx", "atr_pct", "market_context", "ret_5", "vol_ratio", "confidence"}
FORBIDDEN = re.compile(r"(size|qty|quantity|amount|position.?limit|risk|halt|daily.?loss|live|kill|mode|exposure)", re.I)
STATUSES = ("PROPOSED", "TESTING", "ACTIVE", "RETIRED")


class LessonInvalid(ValueError):
    pass


def validate_action(action: dict) -> dict:
    if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
        raise LessonInvalid(f"action type must be one of {sorted(ACTION_TYPES)}")
    for k in action:
        if k not in ("type", "value") or FORBIDDEN.search(str(k)):
            raise LessonInvalid(f"lessons may not set '{k}'")
    t, v = action["type"], action.get("value")
    if t in ("reduce_confidence", "prefer"):
        v = float(v if v is not None else 10)
        if not 0 < v <= 30:
            raise LessonInvalid("confidence adjustment must be 1-30 points")
    elif t == "tighten_stop":
        v = float(v if v is not None else 0.8)
        if not 0.3 <= v < 1:
            raise LessonInvalid("tighten_stop factor must be in [0.3, 1)")
    else:
        v = None
    return {"type": t, "value": v}


def validate_trigger(trigger: dict) -> dict:
    if not isinstance(trigger, dict):
        raise LessonInvalid("trigger must be an object")
    out = {}
    for k, v in trigger.items():
        if k not in TRIGGER_KEYS:
            continue  # unknown keys are dropped, never interpreted
        if k == "ranges":
            rng = {}
            for f, bounds in (v or {}).items():
                if f in RANGE_FEATURES and isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                    rng[f] = [None if b is None else float(b) for b in bounds]
            if rng:
                out["ranges"] = rng
        elif isinstance(v, list):
            out[k] = [str(x)[:40] for x in v][:20]
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v if not isinstance(v, str) else v[:40]
    if not out:
        raise LessonInvalid("trigger needs at least one recognised condition")
    return out


def clean_text(text: str) -> str:
    t = re.sub(r"https?://\S+", "", text or "")
    t = re.sub(r"[<>{}`]", "", t)
    return " ".join(t.split())[:400]


def matches(trigger: dict, f: dict) -> bool:
    def in_list(key, feat):
        vals = trigger.get(key)
        return vals is None or (f.get(feat) is not None and str(f.get(feat)).upper() in {str(x).upper() for x in vals})

    if not (in_list("symbols", "symbol") and in_list("sectors", "sector") and in_list("event_types", "event_type")):
        return False
    for k in ("style", "source", "time_of_day", "vix_regime", "weekday", "algo_id", "above_sma50"):
        if k in trigger and str(trigger[k]).lower() != str(f.get(k)).lower():
            return False
    for feat, (lo, hi) in (trigger.get("ranges") or {}).items():
        v = f.get(feat)
        if v is None or (lo is not None and v < lo) or (hi is not None and v > hi):
            return False
    return True


def next_code(db: Session) -> str:
    n = (db.scalar(select(func.count()).select_from(Lesson)) or 0) + 1
    return f"L-{n:03d}"


def create_proposed(db: Session, text: str, trigger: dict, action: dict, source: str) -> Lesson:
    trig, act, txt = validate_trigger(trigger), validate_action(action), clean_text(text)
    for existing in db.scalars(select(Lesson).where(Lesson.status != "RETIRED")):
        if existing.trigger == trig and existing.action["type"] == act["type"]:
            return existing  # same lesson already tracked: caller adds evidence
    lesson = Lesson(code=next_code(db), text=txt, trigger=trig, action=act, status="PROPOSED", source=source,
                    embedding=embed(txt + " " + str(trig)))
    db.add(lesson)
    db.flush()
    log.info("lesson proposed", extra={"lesson": lesson.code, "source": source})
    return lesson


def add_evidence(db: Session, lesson: Lesson, supports: bool, trade_id: int | None, review_id: int | None,
                 day, note: str | None = None) -> None:
    dup = db.scalar(select(LessonEvidence).where(LessonEvidence.lesson_id == lesson.id,
                                                LessonEvidence.trade_id == trade_id, LessonEvidence.review_id == review_id))
    if dup:
        return
    db.add(LessonEvidence(lesson_id=lesson.id, trade_id=trade_id, review_id=review_id, supports=supports, day=day, note=note))
    if supports:
        lesson.support_count += 1
    else:
        lesson.contradiction_count += 1


def evidence_supports(lesson: Lesson, trade: Trade, review: TradeReview | None) -> bool:
    """Does this closed trade support the lesson? Avoid-type lessons are supported by bad entries/losses."""
    loss = (trade.profit_loss or 0) <= 0
    bad = review is not None and review.decision_quality == "unsound"
    t = lesson.action["type"]
    if t in ("avoid", "reduce_confidence", "require_confirmation"):
        return loss or bad
    if t == "tighten_stop":
        return review is not None and review.classification in ("good-entry/bad-exit", "target-too-greedy") or loss
    return not loss  # prefer


def distinct_days(db: Session, lesson: Lesson, supports: bool = True) -> int:
    return db.scalar(select(func.count(func.distinct(LessonEvidence.day))).where(
        LessonEvidence.lesson_id == lesson.id, LessonEvidence.supports.is_(supports))) or 0


def promote_proposed(db: Session) -> list[str]:
    cfg = settings_store.get(db, "lessons")
    moved = []
    for l in db.scalars(select(Lesson).where(Lesson.status == "PROPOSED")):
        if (l.support_count >= cfg["min_support_for_testing"] and distinct_days(db, l) >= cfg["min_distinct_days"]
                and l.support_count > l.contradiction_count):
            l.status = "TESTING"
            moved.append(l.code)
    db.commit()
    return moved


def is_holdout(trade: Trade) -> bool:
    """~20% of trading days are a permanent holdout: lessons are never validated or tuned on them."""
    return to_ist(trade.created_at).toordinal() % 5 == 0


def simulate_effect(lesson: Lesson, trade: Trade, threshold: float) -> float:
    """P&L delta if the lesson had been active for this already-closed trade (approximation)."""
    pnl = trade.profit_loss or 0
    t, v = lesson.action["type"], lesson.action.get("value")
    if t == "avoid":
        return -pnl
    if t == "reduce_confidence":
        conf = (trade.setup or {}).get("confidence")
        return -pnl if conf is not None and conf - v < threshold else 0.0
    if t == "require_confirmation":
        return -pnl if not (trade.setup or {}).get("technicals_agree", False) else 0.0
    if t == "tighten_stop":
        if trade.exit_reason == "stop_loss" and pnl < 0:
            return -pnl * (1 - v)  # smaller loss
        return 0.0
    if t == "prefer":
        return 0.0
    return 0.0


def validate_out_of_sample(db: Session, lesson: Lesson) -> dict:
    cfg = settings_store.get(db, "lessons")
    threshold = settings_store.get(db, "trade_gate")["confidence_threshold"]
    evidence_ids = set(db.scalars(select(LessonEvidence.trade_id).where(LessonEvidence.lesson_id == lesson.id)))
    pool = [t for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.CLOSED, Trade.instrument == "EQ"))
            if t.id not in evidence_ids and not is_holdout(t) and matches(lesson.trigger, t.setup or {})]
    effects = [simulate_effect(lesson, t, threshold) for t in pool]
    n = len(effects)
    improvement = sum(effects) / n if n else None
    passed = n >= 5 and improvement is not None and improvement > cfg["min_oos_improvement"]
    result = {"n": n, "avg_pnl_delta": None if improvement is None else round(improvement, 2),
              "total_pnl_delta": round(sum(effects), 2), "passed": passed, "at": utcnow().isoformat(),
              "note": "insufficient data" if n < 5 else None}
    lesson.validation, lesson.last_validated = result, utcnow()
    lesson.win_rate_impact = None if improvement is None else round(improvement, 2)
    db.commit()
    return result


def approve(db: Session, lesson: Lesson) -> Lesson:
    """Called only from the authenticated UI. TESTING + passed validation -> ACTIVE."""
    cfg = settings_store.get(db, "lessons")
    if lesson.status != "TESTING":
        raise LessonInvalid("only TESTING lessons can be approved")
    if not (lesson.validation or {}).get("passed"):
        raise LessonInvalid("out-of-sample validation has not passed")
    active = db.scalar(select(func.count()).select_from(Lesson).where(Lesson.status == "ACTIVE")) or 0
    if active >= cfg["max_active"]:
        raise LessonInvalid(f"max active lessons ({cfg['max_active']}) reached: retire one first")
    lesson.status, lesson.approved_at = "ACTIVE", utcnow()
    lesson.expires_at = utcnow() + timedelta(days=cfg["revalidate_days"])
    db.commit()
    log.warning("lesson ACTIVATED", extra={"lesson": lesson.code})
    return lesson


def retire(db: Session, lesson: Lesson, reason: str) -> Lesson:
    lesson.status, lesson.retired_reason = "RETIRED", reason[:500]
    db.commit()
    log.info("lesson retired", extra={"lesson": lesson.code, "reason": reason})
    return lesson


def revalidate_active(db: Session) -> list[str]:
    cfg = settings_store.get(db, "lessons")
    retired = []
    for l in db.scalars(select(Lesson).where(Lesson.status == "ACTIVE")):
        if l.expires_at and l.expires_at > utcnow():
            continue
        res = validate_out_of_sample(db, l)
        if not res["passed"] or l.contradiction_count >= l.support_count:
            retire(db, l, f"re-validation failed: {res}")
            retired.append(l.code)
        else:
            l.expires_at = utcnow() + timedelta(days=cfg["revalidate_days"])
    db.commit()
    return retired
