from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena.algos import parser
from syena.algos import service as svc
from syena.algos.rules import RuleError, describe_rule, evaluate, snapshot, warmup_bars
from syena.api.common import market_data, ser
from syena.api.deps import AuthContext, authenticated
from syena.db import get_db, utcnow
from syena.market.data import INTERVALS
from syena.models import Algo, Decision, Screen, Signal, Style, Trade, TradeStatus, User
from syena.services.auth import verify_credentials

router = APIRouter(prefix="/api/algos", tags=["algos"], dependencies=[Depends(authenticated)])


class BuildIn(BaseModel):
    text: str = Field(max_length=2000)
    style: Style
    run_mode: str = Field(pattern="^(BACKTEST|PAPER|LIVE)$")
    symbols: list[str] = Field(default_factory=list)
    timeframe: str = "15minute"
    name: str | None = Field(default=None, max_length=120)


class PatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    run_mode: str | None = Field(default=None, pattern="^(BACKTEST|PAPER|LIVE)$")
    symbols: list[str] | None = None
    timeframe: str | None = None
    style: Style | None = None
    instrument: str | None = Field(default=None, pattern="^(EQ|OPT)$")  # OPT: buy call options on signals (paper)


class ReauthIn(BaseModel):
    password: str = Field(max_length=256)
    totp: str = Field(max_length=10)


def algo_out(a: Algo, db: Session) -> dict:
    return ser(a, extra={"readable": describe_rule(a.parsed_rule) if a.parsed_rule else None,
                         "backtest_result": None, "has_backtest": a.backtest_result is not None,
                         "metrics": (a.backtest_result or {}).get("metrics"),
                         "screen_linked": db.scalar(select(Screen.id).where(Screen.algo_id == a.id).limit(1)) is not None})


@router.post("/parse")
def parse_preview(body: BuildIn, db: Session = Depends(get_db)):
    return parser.parse(body.text, db=db).as_dict()


@router.get("")
def list_algos(db: Session = Depends(get_db)):
    return [algo_out(a, db) for a in db.scalars(select(Algo).where(Algo.status != "DELETED").order_by(Algo.id.desc()))]


@router.get("/patterns")
def pattern_catalog():
    """Every candlestick pattern the rule language understands, with what it means and how to write it."""
    from syena.algos import patterns

    return {"patterns": patterns.catalog(),
            "modifiers": [{"phrase": "confirmed hammer", "meaning": "act only after the next candle closes beyond the pattern candle"},
                          {"phrase": "hammer at support", "meaning": "the pattern's low touches a support zone and closes above it"},
                          {"phrase": "shooting star at resistance", "meaning": "the high touches a resistance zone and closes below it"},
                          {"phrase": "bullish engulfing on high volume", "meaning": "volume above its 20-candle average"},
                          {"phrase": "morning star in an uptrend", "meaning": "close above the 50-candle average"},
                          {"phrase": "…, sell when any bearish pattern", "meaning": "exit on the first bearish reversal pattern"}]}


@router.post("")
def build(body: BuildIn, db: Session = Depends(get_db)):
    if body.timeframe not in INTERVALS:
        raise HTTPException(400, f"timeframe must be one of {INTERVALS}")
    algo, out = svc.build(db, body.text, body.style, body.run_mode, body.symbols, body.timeframe, body.name)
    return {"algo": algo_out(algo, db), "parse": out}


def _get(db, algo_id) -> Algo:
    a = db.get(Algo, algo_id)
    if a is None:
        raise HTTPException(404, "algo not found")
    return a


@router.get("/{algo_id}")
def inspect_algo(algo_id: int, db: Session = Depends(get_db)):
    a = _get(db, algo_id)
    signals = [ser(s) for s in db.scalars(select(Signal).where(Signal.algo_id == a.id).order_by(Signal.id.desc()).limit(50))]
    decisions = [ser(d, exclude=("inputs_json", "similar_trades")) for d in
                 db.scalars(select(Decision).where(Decision.algo_id == a.id).order_by(Decision.id.desc()).limit(50))]
    return {"algo": ser(a, extra={"readable": describe_rule(a.parsed_rule) if a.parsed_rule else None}),
            "signals": signals, "decisions": decisions, "gate": svc.gate_status(db, a) if a.kind == "rule" else None}


@router.get("/{algo_id}/live-values")
def live_values(algo_id: int, md=Depends(market_data), db: Session = Depends(get_db)):
    """Current indicator values vs thresholds for each symbol (Algo Inspector)."""
    a = _get(db, algo_id)
    if not a.parsed_rule:
        raise HTTPException(400, "no rule")
    now = utcnow()
    lookback = warmup_bars(a.parsed_rule) * svc.BAR_MINUTES[a.timeframe]
    out = []
    for sym in a.symbols:
        df = md.candles(sym, a.timeframe, now - timedelta(minutes=lookback * 3 + 4320), now)
        df = svc.completed_bars(df, a.timeframe, now)
        if len(df) < 3:
            out.append({"symbol": sym, "error": "not enough candles"})
            continue
        out.append({"symbol": sym, "bar": df.index[-1].isoformat(), "values": snapshot(df, a.parsed_rule),
                    "entry_now": bool(evaluate(df, a.parsed_rule["entry"]).iloc[-1]),
                    "exit_now": bool(evaluate(df, a.parsed_rule["exit"]).iloc[-1]) if a.parsed_rule.get("exit") else None})
    return out


@router.patch("/{algo_id}")
def patch_algo(algo_id: int, body: PatchIn, db: Session = Depends(get_db)):
    a = _get(db, algo_id)
    if a.status == "RUNNING":
        raise HTTPException(409, "stop the algo before editing")
    changed = False
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "timeframe" and v not in INTERVALS:
            raise HTTPException(400, "bad timeframe")
        if k == "symbols":
            v = list(dict.fromkeys(s.strip().upper() for s in v if s.strip()))
        if k in ("symbols", "timeframe", "style") and v != getattr(a, k):
            changed = True
        setattr(a, k, v)
    if changed and a.status in ("CONFIRMED", "STOPPED"):  # the old backtest no longer describes this algo
        a.status, a.backtest_passed, a.live_enabled = "DRAFT", False, False
    a.live_enabled = a.live_enabled and a.run_mode == "LIVE"
    db.commit()
    return algo_out(a, db)


@router.put("/{algo_id}/rule")
def edit_rule(algo_id: int, rule: dict, db: Session = Depends(get_db)):
    a = _get(db, algo_id)
    try:
        svc.edit_rule(db, a, rule)
    except (RuleError, KeyError, TypeError) as e:
        raise HTTPException(400, f"invalid rule: {e}")
    return algo_out(a, db)


@router.post("/{algo_id}/backtest")
def backtest(algo_id: int, days: int = 365, md=Depends(market_data), db: Session = Depends(get_db)):
    a = _get(db, algo_id)
    try:
        return svc.run_backtest(db, a, md, days=min(days, 2000))
    except RuleError as e:
        raise HTTPException(400, str(e))


class TimeframesIn(BaseModel):
    timeframes: list[str] | None = None


_tf_jobs: dict[int, dict] = {}


@router.post("/{algo_id}/backtest-timeframes")
def backtest_timeframes(algo_id: int, body: TimeframesIn, md=Depends(market_data), db: Session = Depends(get_db)):
    """Backtest the rule on 1m, 2m, 5m, 15m, 1h, 4h and daily candles (in the background: intraday history takes a while)."""
    import threading

    from syena.db import SessionLocal

    a = _get(db, algo_id)
    if not a.parsed_rule or not a.symbols:
        raise HTTPException(400, "confirm the rule and add stocks first")
    if _tf_jobs.get(algo_id, {}).get("status") == "running":
        raise HTTPException(409, "already running for this algo")
    del md  # checked that prices are connected; the job opens its own connection

    def work():
        from syena.market import source

        _tf_jobs[algo_id] = {"status": "running"}
        try:
            with SessionLocal() as s:
                algo = s.get(type(a), algo_id)
                svc.backtest_all_timeframes(s, algo, source.get_market_data(s), body.timeframes)
            _tf_jobs[algo_id] = {"status": "done"}
        except Exception as e:
            _tf_jobs[algo_id] = {"status": "failed", "error": str(e)[:300]}

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@router.get("/{algo_id}/backtest-timeframes")
def backtest_timeframes_result(algo_id: int, db: Session = Depends(get_db)):
    _get(db, algo_id)
    return {"latest": svc.timeframe_results(db, algo_id), "job": _tf_jobs.get(algo_id, {"status": None}),
            "progress": svc._tf_progress.get(algo_id), "timeframes": {k: {"label": v[0], "days": v[1]} for k, v in svc.TF_WINDOWS.items()}}


@router.get("/{algo_id}/backtest")
def get_backtest(algo_id: int, db: Session = Depends(get_db)):
    return _get(db, algo_id).backtest_result


@router.post("/{algo_id}/start")
def start(algo_id: int, db: Session = Depends(get_db)):
    try:
        return algo_out(svc.start(db, _get(db, algo_id)), db)
    except RuleError as e:
        raise HTTPException(400, str(e))


@router.delete("/{algo_id}")
def delete_algo(algo_id: int, db: Session = Depends(get_db)):
    """Delete a strategy. Refused while it has open positions. If it has ever produced signals, decisions or
    trades it is hidden (status DELETED) so the Decision Log keeps its history; otherwise it is removed."""
    a = _get(db, algo_id)
    if a.kind == "news":
        raise HTTPException(400, "The AI news strategy is built in and can't be deleted. Stop it instead, or switch news "
                                 "trading off in Settings.")
    open_n = db.scalar(select(func.count()).select_from(Trade).where(
        Trade.algo_id == a.id, Trade.status.in_((TradeStatus.OPEN, TradeStatus.PENDING)))) or 0
    if open_n:
        raise HTTPException(409, f"This algo still has {open_n} open or pending position(s). Close them first "
                                 "(Trades page), then delete it.")
    used = any(db.scalar(select(m.id).where(m.algo_id == a.id).limit(1)) for m in (Signal, Decision, Trade))
    if used:
        a.status, a.live_enabled = "DELETED", False
        a.name = a.name if a.name.endswith(" (deleted)") else f"{a.name[:110]} (deleted)"
    else:
        db.delete(a)
    db.commit()
    return {"deleted": True, "kept_history": used}


@router.post("/{algo_id}/stop")
def stop(algo_id: int, db: Session = Depends(get_db)):
    return algo_out(svc.stop(db, _get(db, algo_id)), db)


@router.post("/{algo_id}/enable-live")
def enable_live(algo_id: int, body: ReauthIn, ctx: AuthContext = Depends(authenticated), db: Session = Depends(get_db)):
    user = db.get(User, ctx.user.id)
    fail = verify_credentials(db, user, body.password, body.totp)
    db.commit()
    if fail:
        raise HTTPException(403, "Re-authentication failed.")
    try:
        return algo_out(svc.enable_live(db, _get(db, algo_id)), db)
    except RuleError as e:
        raise HTTPException(400, str(e))


@router.post("/{algo_id}/disable-live")
def disable_live(algo_id: int, db: Session = Depends(get_db)):
    a = _get(db, algo_id)
    a.live_enabled = False
    db.commit()
    return algo_out(a, db)
