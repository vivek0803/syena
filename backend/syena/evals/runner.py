"""EvalRun bookkeeping + the registry of suites used by the API ("Run evals") and CI."""
import logging
import traceback
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import utcnow
from syena.models import AICall, EvalResult, EvalRun

log = logging.getLogger("syena.evals")


def evals_dir() -> Path:
    configured = get_settings().evals_dir
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "evals"


def start(db: Session, suite: str, config: dict | None = None, triggered_by: str = "manual") -> EvalRun:
    run = EvalRun(suite=suite, config=config or {}, triggered_by=triggered_by)
    db.add(run)
    db.commit()
    return run


def add_result(db: Session, run: EvalRun, case_id: str, passed: bool, *, score=None, input=None, expected=None,
               actual=None, detail=None) -> None:
    db.add(EvalResult(run_id=run.id, case_id=str(case_id)[:128], passed=passed, score=score, input=input,
                      expected=expected, actual=actual, detail=(detail or "")[:2000] or None))


def finish(db: Session, run: EvalRun, metrics: dict, passed: bool | None, n_cases: int) -> EvalRun:
    from syena.util import jsonable

    run.cost_usd = float(db.scalar(select(func.coalesce(func.sum(AICall.cost_usd), 0.0)).where(AICall.eval_run_id == run.id)) or 0)
    lat = db.scalar(select(func.avg(AICall.latency_ms)).where(AICall.eval_run_id == run.id))
    metrics = {**metrics, "_cost_usd": round(run.cost_usd, 4), "_avg_latency_ms": None if lat is None else int(lat)}
    run.metrics, run.passed, run.n_cases, run.status = jsonable(metrics), passed, n_cases, "DONE"
    run.finished_at = utcnow()
    db.commit()
    log.info("eval finished", extra={"suite": run.suite, "passed": passed, "n": n_cases})
    return run


def fail(db: Session, run: EvalRun, err: Exception) -> EvalRun:
    run.status, run.error, run.finished_at = "ERROR", "".join(traceback.format_exception_only(err))[:2000], utcnow()
    db.commit()
    return run


def suites() -> dict[str, Callable]:
    from syena.evals import judge, learning_eval, parser_eval, signal_eval, triage_eval

    return {
        "parser": parser_eval.run_suite,
        "signal_replay": signal_eval.run_suite,
        "triage": triage_eval.run_suite,
        "reasoning_judge": judge.run_suite,
        "learning": learning_eval.run_suite,
    }


def compare(db: Session, suite: str, configs: list[dict], triggered_by: str = "manual") -> list[EvalRun]:
    """17F: run one dataset against several model/prompt configs and return the runs side by side."""
    fn = suites()[suite]
    return [fn(db, cfg, triggered_by) for cfg in configs]
