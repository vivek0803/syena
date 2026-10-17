"""Algo Builder lifecycle: build (parse) -> confirm/edit -> backtest -> start (paper; live after the gate),
plus the runner that evaluates RUNNING algos on each completed bar."""
import logging
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.algos import parser
from syena.algos.backtest import BacktestConfig, combine, run
from syena.algos.rules import RuleError, canon_rule, describe_rule, evaluate, snapshot, warmup_bars
from syena.db import utcnow
from syena.execution import engine, live_gate
from syena.execution.checks import Check
from syena.learning import pretrade
from syena.learning.features import build_setup
from syena.models import Algo, Signal, Style, Trade, TradeStatus
from syena.algos import indicators as ind

log = logging.getLogger("syena.algos")

BAR_MINUTES = {"minute": 1, "3minute": 3, "5minute": 5, "10minute": 10, "15minute": 15, "30minute": 30, "60minute": 60, "day": 1440}


def backtest_config(db: Session, style: Style) -> BacktestConfig:
    s, sz, p = settings_store.get(db, "styles"), settings_store.get(db, "sizing"), settings_store.get(db, "paper")
    intraday = style == Style.INTRADAY
    return BacktestConfig(
        style=style.value, min_amount=sz["min_amount"], max_amount=sz["max_amount"], slippage_bps=p["slippage_bps"],
        default_stop_atr=s["intraday_default_stop_atr" if intraday else "swing_default_stop_atr"],
        default_target_atr=s["intraday_default_target_atr" if intraday else "swing_default_target_atr"],
        max_hold_days=s["swing_max_holding_days"], entry_cutoff=s["intraday_entry_cutoff"], square_off=s["intraday_square_off"],
    )


def build(db: Session, text: str, style: Style, run_mode: str, symbols: list[str], timeframe: str, name: str | None) -> tuple[Algo, dict]:
    out = parser.parse(text, db=db)
    algo = Algo(name=(name or text[:60]).strip(), english_text=text, style=style, run_mode=run_mode,
                symbols=[s.strip().upper() for s in symbols if s.strip()], timeframe=timeframe,
                parsed_rule=out.rule, parser_source=out.source, parser_prompt_version=out.prompt_version,
                status="DRAFT")
    db.add(algo)
    db.commit()
    log.info("algo built", extra={"algo_id": algo.id, "parse_status": out.status})
    return algo, out.as_dict()


def edit_rule(db: Session, algo: Algo, rule: dict) -> Algo:
    algo.parsed_rule = canon_rule(rule)
    algo.parser_source = "edited"
    algo.status, algo.backtest_passed, algo.live_enabled = "DRAFT", False, False
    algo.updated_at = utcnow()
    db.commit()
    return algo


def run_backtest(db: Session, algo: Algo, md, days: int = 365) -> dict:
    if not algo.parsed_rule:
        raise RuleError("no confirmed rule")
    if not algo.symbols:
        raise RuleError("add at least one symbol")
    end = utcnow()
    start = end - timedelta(days=days if algo.timeframe == "day" else min(days, 180))
    cfg = backtest_config(db, algo.style)
    per = [run(md.candles(sym, algo.timeframe, start, end), algo.parsed_rule, cfg, sym) for sym in algo.symbols]
    report = combine(per, cfg)
    report["config"] = {"start": start.isoformat(), "end": end.isoformat(), "timeframe": algo.timeframe,
                        "style": algo.style.value, "symbols": algo.symbols}
    g = settings_store.get(db, "live_gate")
    m = report["metrics"]
    algo.backtest_result = report
    algo.backtest_passed = bool(m["trades"] >= g["min_backtest_trades"] and (m["win_rate"] or 0) >= g["min_backtest_win_rate"]
                                and (m["sharpe"] or 0) >= g["min_backtest_sharpe"]
                                and (m["max_drawdown_pct"] or 999) <= g["max_backtest_drawdown_pct"])
    algo.status = "CONFIRMED"
    algo.updated_at = utcnow()
    db.commit()
    log.info("backtest done", extra={"algo_id": algo.id, "trades": m["trades"], "passed": algo.backtest_passed})
    return report


def start(db: Session, algo: Algo) -> Algo:
    if algo.status not in ("CONFIRMED", "STOPPED") or not algo.backtest_result:
        raise RuleError("confirm the rule and run a backtest before starting")
    if algo.run_mode == "BACKTEST":
        raise RuleError("run mode is BACKTEST: change it to PAPER or LIVE to start")
    algo.status, algo.started_at = "RUNNING", utcnow()
    db.commit()
    return algo


def stop(db: Session, algo: Algo) -> Algo:
    algo.status = "STOPPED"
    db.commit()
    return algo


def gate_status(db: Session, algo: Algo) -> list[dict]:
    return [c.as_dict() for c in live_gate.evaluate(db, algo)]


def enable_live(db: Session, algo: Algo) -> Algo:
    """Caller (API) has already re-verified password + TOTP."""
    fails = [c for c in live_gate.evaluate(db, algo) if not c.passed]
    if fails:
        raise RuleError("LIVE gate not passed: " + "; ".join(f"{c.name}: {c.detail}" for c in fails))
    if algo.run_mode != "LIVE":
        raise RuleError("set the algo's run mode to LIVE first")
    algo.live_enabled = True
    db.commit()
    log.warning("algo LIVE-enabled", extra={"algo_id": algo.id})
    return algo


# ---------------------------------------------------------------- runner
def _risk_prices(rule: dict, df: pd.DataFrame, entry: float, style: Style, db: Session, stop_factor: float) -> tuple[float, float]:
    cfg = backtest_config(db, style)
    a = float(ind.atr(df, 14).iloc[-1])

    def lvl(spec, default_mult, sign, factor=1.0):
        spec = spec or {"type": "atr", "mult": default_mult}
        dist = entry * spec["value"] / 100 if spec["type"] == "pct" else spec["mult"] * a
        return round(entry + sign * dist * factor, 2)

    return lvl(rule.get("stop_loss"), cfg.default_stop_atr, -1, stop_factor), lvl(rule.get("target"), cfg.default_target_atr, 1)


def completed_bars(df: pd.DataFrame, timeframe: str, now: datetime) -> pd.DataFrame:
    """Drop the still-forming last bar so signals only use closed candles."""
    if df.empty:
        return df
    last = df.index[-1].to_pydatetime()
    if timeframe == "day":
        return df if now - last >= timedelta(hours=10) else df.iloc[:-1]  # today's daily bar closes 15:30
    return df if now >= last + timedelta(minutes=BAR_MINUTES[timeframe]) else df.iloc[:-1]


def tick_algo(db: Session, algo: Algo, md, now: datetime | None = None, broker_factory=None) -> list[int]:
    """Evaluate one RUNNING algo on its symbols. Returns decision ids."""
    now = now or utcnow()
    rule = algo.parsed_rule
    lookback = warmup_bars(rule) * BAR_MINUTES[algo.timeframe]
    start = now - timedelta(minutes=lookback * (3 if algo.timeframe != "day" else 2) + 3 * 1440)
    out = []
    for sym in algo.symbols:
        df = completed_bars(md.candles(sym, algo.timeframe, start, now), algo.timeframe, now)
        if len(df) < 3:
            continue
        bar_ts = df.index[-1].isoformat()
        open_trade = db.scalar(select(Trade).where(Trade.algo_id == algo.id, Trade.stock == sym, Trade.status == TradeStatus.OPEN))
        if open_trade:
            if rule.get("exit") and bool(evaluate(df, rule["exit"]).iloc[-1]):
                d = engine.exit_trade(db, open_trade, "exit_rule", float(df["close"].iloc[-1]), broker_factory=broker_factory,
                                      detail={"rule": describe_rule(rule)})
                out.append(d.id)
            continue
        if not bool(evaluate(df, rule["entry"]).iloc[-1]):
            continue
        price = md.ltp([sym]).get(sym) or float(df["close"].iloc[-1])
        ctx = md.index_snapshot() or {}
        setup = build_setup(symbol=sym, style=algo.style.value, source="algo", ts=now, df=df, algo_id=algo.id,
                            vix=(ctx.get("VIX") or {}).get("last"))
        setup["technicals_agree"] = True  # the rule itself is the technical confirmation
        sig = Signal(source="algo", algo_id=algo.id, symbol=sym, action="BUY", confidence=100.0,
                     reasoning=f"rule fired on bar {bar_ts}: {describe_rule(rule).splitlines()[0]}",
                     indicators={"snapshot": snapshot(df, rule)}, price_at_signal=price, ts=now)
        db.add(sig)
        db.flush()
        consult = pretrade.consult(db, setup, None)
        stop, target = _risk_prices(rule, df, price, algo.style, db, consult.stop_factor)
        intent = engine.TradeIntent(
            symbol=sym, style=algo.style, source="algo", ref_price=price, algo_id=algo.id, signal_id=sig.id,
            stop_loss=stop, target=target, algo_condition=algo.english_text,
            exit_condition=describe_rule(rule).split("\n")[1] if rule.get("exit") else None,
            idempotency_key=f"algo{algo.id}-{sym}-{bar_ts}", auto_sell=True, extra_checks=[Check("rule_signal", True, sig.reasoning), *consult.checks],
            reasoning={"rule": describe_rule(rule), "indicators": snapshot(df, rule)},
            inputs={"bar_ts": bar_ts, "timeframe": algo.timeframe, "price": price},
            lessons_consulted=consult.consulted, lessons_applied=consult.applied,
            lessons_would_apply=consult.would_apply, similar_trades=consult.similar, setup=setup,
        )
        d = engine.enter(db, intent, broker_factory, now=now)
        pretrade.record(db, d.id, consult)
        out.append(d.id)
    return out
