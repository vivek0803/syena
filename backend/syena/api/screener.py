"""Screener API: all-stocks table, data refresh, Screener.in CSV import, saved screens with daily alerts."""
import threading

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.api.common import ser
from syena.api.deps import authenticated
from syena.db import SessionLocal, get_db
from syena.models import Algo, Screen, ScreenRun
from syena.screener import data, lists, screens

router = APIRouter(prefix="/api/screener", tags=["screener"], dependencies=[Depends(authenticated)])
_jobs: dict[str, str] = {}


def _md(db):
    from syena.market import source

    return source.get_market_data(db)


@router.get("/status")
def screener_status(db: Session = Depends(get_db)):
    return {**data.status(db), "jobs": dict(_jobs), "fields": {k: v[2] for k, v in screens.FIELDS.items()}}


@router.get("/stocks")
def stocks(where: str = "", q: str = "", sort: str = "market_cap", desc: bool = True, limit: int = 100, offset: int = 0,
           db: Session = Depends(get_db)):
    day = screens.latest_day(db)
    try:
        rows = screens.screen_rows(db, where, day)[1] if where.strip() else screens.rows_for_day(db, day)
    except screens.ScreenError as e:
        raise HTTPException(400, str(e))
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["symbol"].lower() or ql in (r.get("name") or "").lower()]
    if sort not in screens.FIELDS and sort not in ("symbol", "name"):
        sort = "market_cap"
    have = sorted((r for r in rows if r.get(sort) is not None), key=lambda r: r[sort], reverse=desc)
    rows = have + [r for r in rows if r.get(sort) is None]  # missing values always last
    limit = max(1, min(limit, 500))
    return {"day": day.isoformat() if day else None, "total": len(rows), "rows": rows[offset: offset + limit]}


# ---------------------------------------------------------------- stock pickers (algo builder)
@router.get("/company/{symbol}")
def company(symbol: str, db: Session = Depends(get_db)):
    """A screener.in-style company page."""
    from syena.market import source
    from syena.screener import company as co

    try:
        md = source.get_market_data(db)
    except Exception:
        md = None
    try:
        return co.page(db, symbol, md)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/fields")
def fields():
    """Every field the screen language knows, with its label and aliases (for the query box's suggestions)."""
    alias = {}
    for a, c in screens.ALIASES:
        alias.setdefault(c, []).append(a)
    return [{"key": k, "label": v[2], "aliases": alias.get(k, [])[:4]} for k, v in screens.FIELDS.items()]


@router.get("/search")
def search(q: str = "", db: Session = Depends(get_db)):
    return lists.search(db, q)


@router.get("/lists")
def stock_lists(db: Session = Depends(get_db)):
    return {**lists.catalog(db), "max": None}


@router.get("/list")
def stock_list(kind: str, key: str, db: Session = Depends(get_db)):
    try:
        syms = lists.resolve(db, kind, key)
    except lists.ListError as e:
        raise HTTPException(400, str(e))
    return {"symbols": syms, "count": len(syms)}


class RefreshIn(BaseModel):
    what: str = Field(pattern="^(prices|fundamentals)$")
    symbols: list[str] | None = None


def _bg(name: str, fn) -> None:
    def run():
        _jobs[name] = "running"
        try:
            with SessionLocal() as db:
                res = fn(db)
            _jobs[name] = f"done: {res}"
        except Exception as e:
            _jobs[name] = f"failed: {e}"

    threading.Thread(target=run, daemon=True).start()


@router.post("/refresh")
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    if _jobs.get(body.what) == "running":
        raise HTTPException(409, f"{body.what} refresh already running")
    if body.what == "prices":
        md = _md(db)  # fails fast with a clear message if no price source

        def job(s):
            from syena.screener import sectors

            m = _md(s)
            data.sync_universe(s, data.universe_from_md(s, m))
            if sectors.due(s):
                sectors.sync(s)
            return data.refresh_daily(s, m)
    else:
        def job(s):
            if not data.status(s)["stocks"]:  # universe not built yet: build it from the broker's instrument list
                data.sync_universe(s, data.universe_from_md(s, _md(s)))
            return data.refresh_fundamentals(s, symbols=[x.upper() for x in body.symbols] if body.symbols else None)
        md = None
    del md
    _bg(body.what, job)
    return {"started": body.what}


@router.post("/import-csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    if len(content) > 10_000_000:
        raise HTTPException(413, "file too large")
    try:
        return data.import_screener_csv(db, content)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- screens
class ScreenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    conditions: str = Field(min_length=1, max_length=2000)
    signal_text: str | None = Field(default=None, max_length=2000)
    alerts: bool = True
    algo_id: int | None = None
    exit_on_removal: bool = False


def _signal_rule(db, text: str | None) -> dict | None:
    if not text or not text.strip():
        return None
    from syena.algos import parser

    t = text.strip()
    if not t.lower().startswith(("buy", "when", "enter")):
        t = "buy when " + t
    out = parser.parse(t, db=db)
    if out.status != "ok":
        raise HTTPException(400, f"Technical signal: {out.question}")
    return out.rule


def _validate(db, body: ScreenIn) -> dict:
    try:
        node = screens.parse_conditions(body.conditions)
    except screens.ScreenError as e:
        raise HTTPException(400, str(e))
    if body.algo_id is not None:
        a = db.get(Algo, body.algo_id)
        if a is None or a.kind != "rule" or a.status == "DELETED":
            raise HTTPException(400, "linked algo must be one of your rule algos")
    return node


def screen_out(sc: Screen) -> dict:
    readable = None
    try:
        readable = screens.describe(screens.parse_conditions(sc.conditions))
    except screens.ScreenError:
        pass
    return ser(sc, extra={"readable": readable, "n_members": len(sc.members or [])})


@router.get("/screens")
def list_screens(db: Session = Depends(get_db)):
    return [screen_out(s) for s in db.scalars(select(Screen).order_by(Screen.id))]


@router.post("/preview")
def preview(body: ScreenIn, db: Session = Depends(get_db)):
    _validate(db, body)
    node, rows, missing = screens.screen_rows(db, body.conditions)
    return {"readable": screens.describe(node), "count": len(rows), "rows": rows[:100], "missing_data": missing,
            "signal_ok": _signal_rule(db, body.signal_text) is not None if body.signal_text else None}


@router.post("/screens")
def create_screen(body: ScreenIn, db: Session = Depends(get_db)):
    _validate(db, body)
    sc = Screen(name=body.name, conditions=body.conditions, signal_text=body.signal_text or None,
                signal_rule=_signal_rule(db, body.signal_text), alerts=body.alerts, algo_id=body.algo_id,
                exit_on_removal=body.exit_on_removal)
    db.add(sc)
    db.commit()
    return screen_out(sc)


@router.put("/screens/{screen_id}")
def update_screen(screen_id: int, body: ScreenIn, db: Session = Depends(get_db)):
    sc = _get(db, screen_id)
    _validate(db, body)
    sc.name, sc.conditions, sc.alerts, sc.algo_id = body.name, body.conditions, body.alerts, body.algo_id
    sc.exit_on_removal = body.exit_on_removal
    sc.signal_text, sc.signal_rule = body.signal_text or None, _signal_rule(db, body.signal_text)
    db.commit()
    return screen_out(sc)


def _get(db, screen_id: int) -> Screen:
    sc = db.get(Screen, screen_id)
    if sc is None:
        raise HTTPException(404, "screen not found")
    return sc


@router.delete("/screens/{screen_id}")
def delete_screen(screen_id: int, db: Session = Depends(get_db)):
    sc = _get(db, screen_id)
    for r in db.scalars(select(ScreenRun).where(ScreenRun.screen_id == sc.id)):
        db.delete(r)
    db.delete(sc)
    db.commit()
    return {"deleted": True}


@router.get("/screens/{screen_id}")
def screen_detail(screen_id: int, db: Session = Depends(get_db)):
    sc = _get(db, screen_id)
    runs = list(db.scalars(select(ScreenRun).where(ScreenRun.screen_id == sc.id).order_by(ScreenRun.id.desc()).limit(30)))
    members = set(sc.members or [])
    rows = [r for r in screens.rows_for_day(db, screens.latest_day(db)) if r["symbol"] in members]
    rows.sort(key=lambda r: -(r.get("market_cap") or 0))
    return {"screen": screen_out(sc), "members": rows, "runs": [ser(r) for r in runs], "data_note": data_note(db, sc)}


def data_note(db, sc: Screen) -> str | None:
    """Why a screen may show 0 stocks: missing prices or company figures, not the conditions."""
    st = data.status(db)
    if not st["stocks"] or not st["latest_day"]:
        return "Prices aren't loaded yet. Click 'Update prices now' on the Screener page (takes about a minute), then Run now."
    try:
        used = screens.fields_used(screens.parse_conditions(sc.conditions))
    except screens.ScreenError:
        return None
    needs_f = any(screens.FIELDS[f][0] in ("stock", "computed") or f in ("pe", "pb", "ps", "ev_ebitda", "market_cap", "dividend_yield",
                                                            "earnings_yield") for f in used)
    if needs_f and st["with_fundamentals"] < st["stocks"] * 0.5:
        return (f"Only {st['with_fundamentals']} of {st['stocks']} stocks have company figures so far, and P/E, ROE, market cap… "
                "need them. They fill in automatically each night (about 400 a night), or click 'Fetch fundamentals now'.")
    return None


class TradeIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)  # technical entry/exit rule, same language as algos
    style: str = Field(default="SWING", pattern="^(SWING|LONG_TERM)$")
    timeframe: str = Field(default="day", pattern="^(day|60minute|30minute|15minute)$")
    exit_on_removal: bool = True


@router.post("/screens/{screen_id}/algo")
def trade_screen(screen_id: int, body: TradeIn, db: Session = Depends(get_db)):
    """Fundamentals pick the stocks, a technical rule picks the moment: create a rule algo whose stock list is
    this screen (kept in sync after every daily run) and link it. It starts as a draft: backtest, then start it."""
    from syena.algos import service as algo_svc
    from syena.models import Style

    sc = _get(db, screen_id)
    if not sc.members:
        raise HTTPException(400, "No stocks pass this screen yet. Load prices and company figures, then Run now first.")
    by_cap = {r["symbol"]: r.get("market_cap") or 0 for r in screens.rows_for_day(db, screens.latest_day(db))}
    syms = sorted(sc.members, key=lambda x: -by_cap.get(x, 0))
    algo, out = algo_svc.build(db, body.text, Style(body.style), "PAPER", syms, body.timeframe, f"{sc.name} · {body.text[:40]}")
    sc.algo_id, sc.exit_on_removal = algo.id, body.exit_on_removal
    db.commit()
    return {"algo_id": algo.id, "parse": out, "symbols": len(syms)}


@router.post("/screens/{screen_id}/run")
def run_now(screen_id: int, db: Session = Depends(get_db)):
    sc = _get(db, screen_id)
    md = None
    if sc.signal_rule:
        try:
            md = _md(db)
        except Exception:
            md = None
    try:
        run = screens.run_screen(db, sc, md)
    except screens.ScreenError as e:
        raise HTTPException(400, str(e))
    return ser(run)
