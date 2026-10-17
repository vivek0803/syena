"""Reasoning-quality evals (17D, secondary): an LLM judge (a different model from the analyst) scores
groundedness, consistency and absence of hallucination. Judge scores are spot-checked against your labels."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.ai import llm
from syena.ai.prompts import untrusted
from syena.evals import runner
from syena.models import ManualLabel, NewsAnalysis, Signal

DIMS = ("groundedness", "consistency", "no_hallucination")


def run_suite(db: Session, config: dict | None = None, triggered_by: str = "manual"):
    config = config or {}
    models = settings_store.get(db, "models")
    judge_model = config.get("model") or models["judge"]
    run = runner.start(db, "reasoning_judge", {"judge_model": judge_model, "prompt": "reasoning_judge"}, triggered_by)
    try:
        sigs = list(db.scalars(select(Signal).where(Signal.source == "news", Signal.shadow.is_(False), Signal.reasoning.is_not(None))
                               .order_by(Signal.id.desc()).limit(int(config.get("limit", 30)))))
        scores, agreement, skipped = [], [], 0
        for s in sigs:
            if s.model == judge_model:
                skipped += 1  # never let a model grade itself
                continue
            an = next((a for a in db.scalars(select(NewsAnalysis).where(NewsAnalysis.stage == "analysis").order_by(NewsAnalysis.id.desc()).limit(500))
                       if (a.result or {}).get("signal_id") == s.id), None)
            inputs = {"technicals": s.indicators, "market_context": s.market_context, "cited_ids": s.cited,
                      "news_titles": [n["title"] for n in ((an.result or {}).get("inputs") or {}).get("news", [])] if an else None,
                      "analysis_summary": an.summary if an else None}
            output = {"action": s.action, "confidence": s.confidence, "reasoning": s.reasoning}
            r = llm.call(db, "reasoning_judge", "reasoning_judge", model=judge_model, eval_run_id=run.id, max_tokens=500,
                         variables={"inputs": untrusted(json.dumps(inputs, default=str)[:4000], "inputs"),
                                    "output": untrusted(json.dumps(output, default=str)[:3000], "analyst_output")})
            d = r.data or {}
            row = {k: float(d.get(k, 0) or 0) for k in DIMS}
            scores.append(row)
            lab = db.scalar(select(ManualLabel).where(ManualLabel.kind == "judge", ManualLabel.ref_id == s.id))
            if lab:
                agreement.append(sum(abs(row[k] - float(lab.label.get(k, row[k]))) for k in DIMS) / len(DIMS))
            runner.add_result(db, run, f"signal{s.id}", min(row.values()) >= 3, score=sum(row.values()) / 3,
                              input=output, actual=d, expected=lab.label if lab else None)
        n = len(scores)
        metrics = {"n": n, "skipped_same_model": skipped,
                   **{f"avg_{k}": round(sum(r[k] for r in scores) / n, 3) if n else None for k in DIMS},
                   "pct_any_below_3": round(sum(1 for r in scores if min(r.values()) < 3) / n, 3) if n else None,
                   "manual_spot_checks": len(agreement),
                   "judge_vs_manual_mean_abs_diff": round(sum(agreement) / len(agreement), 3) if agreement else None}
        passed = None if n == 0 else (metrics["pct_any_below_3"] or 0) <= 0.2
        return runner.finish(db, run, metrics, passed, n)
    except Exception as e:
        return runner.fail(db, run, e)
