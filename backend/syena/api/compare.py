"""Compare page API: saved lists of stocks, all metrics side by side, technicals, AI pick."""
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.api.common import ser
from syena.api.deps import authenticated
from syena.db import SessionLocal, get_db
from syena.models import Watchlist
from syena.screener import compare
from syena.screener.screens import FIELDS

router = APIRouter(prefix="/api/compare", tags=["compare"], dependencies=[Depends(authenticated)])
_jobs: dict[int, str] = {}


def _md(db):
    from syena.market import source

    return source.get_market_data(db)


class ListIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    symbols: list[str] = Field(default_factory=list)


def _clean_syms(syms: list[str]) -> list[str]:
    return list(dict.fromkeys(s.strip().upper() for s in syms if s and s.strip()))


def _get(db, list_id: int) -> Watchlist:
    wl = db.get(Watchlist, list_id)
    if wl is None:
        raise HTTPException(404, "list not found")
    return wl


@router.get("/fields")
def fields():
    return {"fields": {k: v[2] for k, v in FIELDS.items()}, "technicals": compare.TECH_FIELDS, "max": None}


@router.get("/lists")
def lists(db: Session = Depends(get_db)):
    return [ser(w, exclude=("ai_result",), extra={"count": len(w.symbols or []), "top_pick": (w.ai_result or {}).get("top_pick")})
            for w in db.scalars(select(Watchlist).order_by(Watchlist.id))]


@router.post("/lists")
def create(body: ListIn, db: Session = Depends(get_db)):
    wl = Watchlist(name=body.name.strip(), symbols=_clean_syms(body.symbols))
    db.add(wl)
    db.commit()
    return ser(wl)


@router.put("/lists/{list_id}")
def update(list_id: int, body: ListIn, db: Session = Depends(get_db)):
    wl = _get(db, list_id)
    wl.name, wl.symbols = body.name.strip(), _clean_syms(body.symbols)
    db.commit()
    return ser(wl)


@router.delete("/lists/{list_id}")
def delete(list_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, list_id))
    db.commit()
    return {"deleted": True}


@router.get("/lists/{list_id}")
def detail(list_id: int, db: Session = Depends(get_db)):
    wl = _get(db, list_id)
    syms = wl.symbols or []
    return {"list": ser(wl), "rows": compare.metrics(db, syms), "missing": compare.missing_data(db, syms),
            "job": _jobs.get(wl.id)}


@router.get("/lists/{list_id}/technicals")
def technicals(list_id: int, db: Session = Depends(get_db)):
    wl = _get(db, list_id)
    try:
        md = _md(db)
    except Exception as e:
        raise HTTPException(409, f"Price data isn't connected: {e}")
    return compare.technicals(md, wl.symbols or [], db, live_fetch=compare.LIVE_FETCH)


@router.post("/lists/{list_id}/refresh")
def refresh(list_id: int, db: Session = Depends(get_db)):
    wl = _get(db, list_id)
    if _jobs.get(wl.id) == "running":
        raise HTTPException(409, "already updating")
    _md(db)  # fail fast with a clear message if no price source
    syms = list(wl.symbols or [])

    def run():
        _jobs[wl.id] = "running"
        try:
            with SessionLocal() as s:
                r = compare.refresh(s, _md(s), syms)
            _jobs[wl.id] = f"done: prices for {r['prices']}, company figures for {r['fundamentals']}"
        except Exception as e:
            _jobs[wl.id] = f"failed: {e}"

    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


class AiIn(BaseModel):
    horizon: str = Field(default="LONG_TERM", pattern="^(LONG_TERM|SWING)$")
    note: str | None = Field(default=None, max_length=500)


@router.post("/lists/{list_id}/ai")
def ai(list_id: int, body: AiIn, db: Session = Depends(get_db)):
    from syena.ai import llm

    wl = _get(db, list_id)
    try:
        md = _md(db)
    except Exception:
        md = None  # still compare on fundamentals
    try:
        return compare.ai_compare(db, wl, md, body.horizon, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (llm.LLMUnavailable, llm.BudgetExceeded) as e:
        raise HTTPException(409, str(e))
    except llm.LLMBadOutput:
        raise HTTPException(502, "The AI's answer couldn't be read. Try again.")
