"""Pattern lab: test every candlestick pattern x filter across an index, and turn winners into algos."""
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from syena.algos import patternlab
from syena.api.deps import authenticated
from syena.db import SessionLocal, get_db

router = APIRouter(prefix="/api/pattern-lab", tags=["pattern-lab"], dependencies=[Depends(authenticated)])
_job: dict = {"status": None}


@router.get("")
def get_lab(db: Session = Depends(get_db)):
    return {"latest": patternlab.latest(db), "job": _job, "progress": patternlab.progress() if _job.get("status") == "running" else None,
            "universes": {"nifty50": "Nifty 50", "nifty100": "Nifty 100", "nifty200": "Nifty 200", "nifty500": "Nifty 500"}}


class RunIn(BaseModel):
    universe: str = "nifty200"
    years: int = 5


@router.post("/run")
def run_lab(body: RunIn, db: Session = Depends(get_db)):
    from syena.market import source

    if _job.get("status") == "running":
        raise HTTPException(409, "the pattern lab is already running")
    if not 2 <= body.years <= 10:
        raise HTTPException(400, "years must be 2-10")
    try:
        source.get_market_data(db)
    except Exception as e:
        raise HTTPException(409, f"Price data isn't connected: {e}")

    def work():
        _job.update({"status": "running", "error": None})
        try:
            with SessionLocal() as s:
                out = patternlab.run(s, source.get_market_data(s), body.universe, body.years)
            _job.update({"status": "done", "winners": out["winners"]})
        except Exception as e:
            _job.update({"status": "failed", "error": str(e)[:300]})

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


class AlgoIn(BaseModel):
    pattern: str
    filter: str


@router.post("/algo")
def make_algo(body: AlgoIn, db: Session = Depends(get_db)):
    from syena.screener import lists

    lab = patternlab.latest(db) or {}
    row = next((r for r in lab.get("results") or [] if r["pattern"] == body.pattern and r["filter"] == body.filter), None)
    if row is None:
        raise HTTPException(404, "run the pattern lab first")
    try:
        syms = lists.index_members(db, lab.get("universe", "nifty200"))
        algo, readable = patternlab.create_algo(db, body.pattern, body.filter, syms)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"algo_id": algo.id, "name": algo.name, "readable": readable, "symbols": len(syms)}


# ---------------------------------------------------------------- zone retest strategy (Umar Punjabi method)
_sjob: dict = {"status": None}


@router.get("/strategy")
def get_strategy_lab(db: Session = Depends(get_db)):
    from syena.algos import strategylab

    return {"latest": strategylab.latest(db), "job": _sjob,
            "progress": strategylab.progress() if _sjob.get("status") == "running" else None,
            "variants": {k: v[0] for k, v in strategylab.VARIANTS.items()}}


@router.post("/strategy/run")
def run_strategy_lab(body: RunIn, db: Session = Depends(get_db)):
    from syena.algos import strategylab
    from syena.market import source

    if _sjob.get("status") == "running":
        raise HTTPException(409, "the strategy test is already running")
    if not 2 <= body.years <= 10:
        raise HTTPException(400, "years must be 2-10")
    try:
        source.get_market_data(db)
    except Exception as e:
        raise HTTPException(409, f"Price data isn't connected: {e}")

    def work():
        _sjob.update({"status": "running", "error": None})
        try:
            with SessionLocal() as s:
                out = strategylab.run(s, source.get_market_data(s), body.universe, body.years)
            _sjob.update({"status": "done", "winners": out["winners"]})
        except Exception as e:
            _sjob.update({"status": "failed", "error": str(e)[:300]})

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


class StrategyAlgoIn(BaseModel):
    variant: str


@router.post("/strategy/algo")
def make_strategy_algo(body: StrategyAlgoIn, db: Session = Depends(get_db)):
    from syena.algos import strategylab
    from syena.screener import lists

    if body.variant not in strategylab.VARIANTS:
        raise HTTPException(400, "unknown version")
    lab = strategylab.latest(db) or {}
    try:
        syms = lists.index_members(db, lab.get("universe", "nifty200"))
        algo, readable = strategylab.create_algo(db, body.variant, syms)
    except (ValueError, lists.ListError) as e:
        raise HTTPException(400, str(e))
    return {"algo_id": algo.id, "name": algo.name, "readable": readable, "symbols": len(syms)}
