"""Weekly pattern mining (spec 18E): statistics first, LLM narrative second."""
import logging
import math
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.ai import llm
from syena.learning import lessons as L
from syena.learning.lessons import is_holdout
from syena.models import Trade, TradeStatus

log = logging.getLogger("syena.mining")

DIMENSIONS = ["style", "source", "event_type", "time_of_day", "vix_regime", "weekday", "symbol", "sector", "algo_id"]
TRIGGER_KEY = {"symbol": "symbols", "sector": "sectors", "event_type": "event_types"}


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def mine(db: Session, min_n: int = 8, margin: float = 0.05) -> list[dict]:
    trades = [t for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.CLOSED)) if not is_holdout(t)]
    if len(trades) < min_n * 2:
        return []
    overall = sum(1 for t in trades if (t.profit_loss or 0) > 0) / len(trades)
    groups: dict[tuple, list[Trade]] = defaultdict(list)
    for t in trades:
        s = t.setup or {}
        for d in DIMENSIONS:
            if s.get(d) is not None:
                groups[(d, s[d])].append(t)
    patterns = []
    for (dim, val), ts in groups.items():
        n = len(ts)
        if n < min_n:
            continue
        wins = sum(1 for t in ts if (t.profit_loss or 0) > 0)
        lo, hi = wilson(wins, n)
        avg = sum(t.profit_loss or 0 for t in ts) / n
        if hi < overall - margin:
            kind = "worse"
        elif lo > overall + margin:
            kind = "better"
        else:
            continue
        patterns.append({"dimension": dim, "value": val, "n": n, "win_rate": round(wins / n, 3), "ci95": [round(lo, 3), round(hi, 3)],
                         "overall_win_rate": round(overall, 3), "avg_pnl": round(avg, 2), "kind": kind,
                         "trade_ids": [t.id for t in ts]})
    return patterns


def propose_from_patterns(db: Session, patterns: list[dict]) -> list[str]:
    codes = []
    for p in patterns:
        key = TRIGGER_KEY.get(p["dimension"], p["dimension"])
        trigger = {key: [p["value"]] if key.endswith("s") else p["value"]}
        action = {"type": "reduce_confidence", "value": 10} if p["kind"] == "worse" else {"type": "prefer", "value": 5}
        text = (f"{p['dimension'].replace('_', ' ')} = {p['value']}: win rate {p['win_rate']:.0%} "
                f"(95% CI {p['ci95'][0]:.0%}-{p['ci95'][1]:.0%}) vs {p['overall_win_rate']:.0%} overall over {p['n']} trades")
        if llm.available():
            try:
                r = llm.call(db, "lesson_narrative", "lesson_narrative", tier="triage", variables={"stats": text}, max_tokens=200)
                if r.data and r.data.get("text"):
                    text = f"{r.data['text']} [{text}]"
            except Exception:
                pass
        try:
            lesson = L.create_proposed(db, text, trigger, action, source="mining")
        except L.LessonInvalid:
            continue
        for tid in p["trade_ids"]:
            t = db.get(Trade, tid)
            supports = ((t.profit_loss or 0) <= 0) if p["kind"] == "worse" else ((t.profit_loss or 0) > 0)
            from syena.market.calendar import to_ist

            L.add_evidence(db, lesson, supports, tid, None, to_ist(t.created_at).date(), note="weekly mining")
        codes.append(lesson.code)
    db.commit()
    log.info("weekly mining", extra={"patterns": len(patterns), "lessons": codes})
    return codes
