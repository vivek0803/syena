"""Online evals (17G): score every signal/decision once its outcome is known, track accuracy per algo and
model, and raise drift alerts (accuracy/calibration drop, parser clarification-rate change, cost spikes)."""
import logging
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import events, settings_store
from syena.db import utcnow
from syena.evals import metrics as M
from syena.learning import reviewer
from syena.models import AICall, Alert, Algo, Decision, DecisionType, LessonApplication, Signal, Trade

log = logging.getLogger("syena.online")


def _ret(df: pd.DataFrame, p0: float, at: datetime, after_bars: int | None = None) -> float | None:
    sel = df[df.index >= pd.Timestamp(at)]
    if sel.empty:
        return None
    px = float(sel["close"].iloc[0] if after_bars is None else (sel["close"].iloc[after_bars] if len(sel) > after_bars else float("nan")))
    return None if px != px else round((px / p0 - 1) * 100, 4)


def score_signals(db: Session, md, now: datetime | None = None, limit: int = 200) -> int:
    now = now or utcnow()
    n = 0
    for s in db.scalars(select(Signal).where(Signal.scored_at.is_(None), Signal.price_at_signal.is_not(None),
                                            Signal.ts <= now - timedelta(hours=1)).order_by(Signal.id).limit(limit)):
        try:
            if s.ret_1h is None:
                intra = md.candles(s.symbol, "15minute", s.ts, min(now, s.ts + timedelta(days=3)))
                s.ret_1h = _ret(intra, s.price_at_signal, s.ts + timedelta(hours=1))
            daily = md.candles(s.symbol, "day", s.ts - timedelta(days=1), min(now, s.ts + timedelta(days=12)))
            after = daily[daily.index > pd.Timestamp(s.ts)]
            if s.ret_1d is None and len(after) >= 1:
                s.ret_1d = round((float(after["close"].iloc[0]) / s.price_at_signal - 1) * 100, 4)
            if s.ret_5d is None and len(after) >= 5:
                s.ret_5d = round((float(after["close"].iloc[4]) / s.price_at_signal - 1) * 100, 4)
                s.scored_at = now
            n += 1
        except Exception as e:
            log.info("signal scoring skipped", extra={"signal": s.id, "error": str(e)})
    db.commit()
    return n


def review_unexecuted(db: Session) -> int:
    n = 0
    for d in db.scalars(select(Decision).where(Decision.type.in_((DecisionType.REJECTED, DecisionType.SKIPPED)),
                                              Decision.signal_id.is_not(None), Decision.side == "BUY").order_by(Decision.id.desc()).limit(300)):
        s = db.get(Signal, d.signal_id)
        if s and s.ret_5d is not None and reviewer.review_unexecuted(db, d, s.ret_1d, s.ret_5d):
            n += 1
    return n


def score_lesson_applications(db: Session) -> int:
    """Was the lesson right? Blocking/reducing lessons are right when the trade lost or the skipped signal fell."""
    n = 0
    for a in db.scalars(select(LessonApplication).where(LessonApplication.was_right.is_(None))):
        d = db.get(Decision, a.decision_id)
        if d is None:
            continue
        kind = (a.effect or {}).get("action")
        trade = db.get(Trade, d.trade_id) if d.trade_id else None
        if trade is not None and trade.profit_loss is not None:
            good = trade.profit_loss > 0
        else:
            s = db.get(Signal, d.signal_id) if d.signal_id else None
            if s is None or s.ret_5d is None:
                continue
            good = s.ret_5d > 0.35
        a.was_right = (not good) if kind in ("avoid", "reduce_confidence", "require_confirmation", "tighten_stop") else good
        n += 1
    db.commit()
    return n


def accuracy_by(db: Session, since: datetime, until: datetime) -> dict:
    groups = defaultdict(list)
    for s in db.scalars(select(Signal).where(Signal.ts >= since, Signal.ts < until, Signal.ret_1d.is_not(None))):
        actual = M.direction_label(s.ret_1d)
        key = f"{'algo' if s.algo_id else s.source}:{s.algo_id or ''}|{s.model or 'rule'}{'|shadow' if s.shadow else ''}"
        groups[key].append((s.action, actual, s.confidence))
    out = {}
    for k, rows in groups.items():
        pairs = [(p, a) for p, a, _ in rows]
        out[k] = {"accuracy": M.accuracy(pairs), "calibration": M.calibration([(c, p == a) for p, a, c in rows if p != "HOLD"])}
    return out


def _alert(db: Session, msg: str, payload: dict) -> Alert:
    a = Alert(kind="drift", message=msg, payload=payload)
    db.add(a)
    db.commit()
    events.publish(db, "alert", {"kind": "drift", "message": msg})
    log.warning("drift alert", extra={"alert": msg})
    return a


def drift_check(db: Session, now: datetime | None = None) -> list[str]:
    now = now or utcnow()
    cfg = settings_store.get(db, "evals")
    cur, prev = accuracy_by(db, now - timedelta(days=14), now), accuracy_by(db, now - timedelta(days=42), now - timedelta(days=14))
    msgs = []
    for k, m in cur.items():
        a = m["accuracy"]
        if a["n"] < cfg["min_signal_sample"]:
            continue
        p = prev.get(k, {}).get("accuracy", {})
        if p.get("value") is not None and p["n"] >= cfg["min_signal_sample"] and a["value"] < p["value"] - cfg["drift_accuracy_drop"]:
            msgs.append(f"{k}: accuracy fell {p['value']:.0%} -> {a['value']:.0%} (n={a['n']})")
        ece = m["calibration"]["ece"]
        if ece is not None and ece > cfg["max_calibration_error"]:
            msgs.append(f"{k}: calibration error {ece:.2f} above {cfg['max_calibration_error']}")
    # parser clarification rate (algo builds where no rule could be parsed)
    def clar_rate(a, b):
        rows = list(db.execute(select(Algo.parsed_rule).where(Algo.kind == "rule", Algo.created_at >= a, Algo.created_at < b)))
        return (sum(1 for (r,) in rows if r is None) / len(rows), len(rows)) if rows else (None, 0)
    (c_now, n_now), (c_prev, n_prev) = clar_rate(now - timedelta(days=14), now), clar_rate(now - timedelta(days=42), now - timedelta(days=14))
    if c_now is not None and c_prev is not None and n_now >= 10 and n_prev >= 10 and abs(c_now - c_prev) > 0.2:
        msgs.append(f"parser clarification rate changed {c_prev:.0%} -> {c_now:.0%}")
    # cost per decision spike
    def cost_per_decision(a, b):
        cost = db.scalar(select(func.coalesce(func.sum(AICall.cost_usd), 0.0)).where(AICall.ts >= a, AICall.ts < b, AICall.eval_run_id.is_(None))) or 0
        nd = db.scalar(select(func.count()).select_from(Decision).where(Decision.ts >= a, Decision.ts < b)) or 0
        return (cost / nd) if nd >= 10 else None
    c1, c0 = cost_per_decision(now - timedelta(days=7), now), cost_per_decision(now - timedelta(days=28), now - timedelta(days=7))
    if c1 and c0 and c1 > c0 * cfg["drift_cost_spike_ratio"]:
        msgs.append(f"AI cost per decision spiked ${c0:.4f} -> ${c1:.4f}")
    recent = {a.message for a in db.scalars(select(Alert).where(Alert.kind == "drift", Alert.created_at >= now - timedelta(days=1)))}
    for m in msgs:
        if m not in recent:
            _alert(db, m, {"window_days": 14})
    return msgs


def shadow_vs_champion(db: Session, days: int = 30) -> dict:
    """Compare shadow (challenger) model signals with the champion on the same period."""
    acc = accuracy_by(db, utcnow() - timedelta(days=days), utcnow())
    return {"champion": {k: v for k, v in acc.items() if "shadow" not in k}, "shadow": {k: v for k, v in acc.items() if "shadow" in k}}
