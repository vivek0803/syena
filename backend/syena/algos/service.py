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

BAR_MINUTES = {"minute": 1, "2minute": 2, "3minute": 3, "5minute": 5, "10minute": 10, "15minute": 15, "30minute": 30, "60minute": 60,
               "240minute": 240, "day": 1440}


def backtest_config(db: Session, style: Style) -> BacktestConfig:
    s, sz, p = settings_store.get(db, "styles"), settings_store.get(db, "sizing"), settings_store.get(db, "paper")
    intraday = style == Style.INTRADAY
    return BacktestConfig(
        style=style.value, min_amount=sz["min_amount"], max_amount=sz["max_amount"], slippage_bps=p["slippage_bps"],
        default_stop_atr=s["intraday_default_stop_atr" if intraday else "swing_default_stop_atr"],
        default_target_atr=s["intraday_default_target_atr" if intraday else "swing_default_target_atr"],
        max_hold_days=s["swing_max_holding_days"], entry_cutoff=s["intraday_entry_cutoff"], square_off=s["intraday_square_off"],
        **_trail_cfg(db),
    )


def _trail_cfg(db: Session) -> dict:
    from syena.execution import trailing

    t = trailing.config(db)
    if not t.get("enabled", True):
        return {"trail_activate_r": None}
    return {"trail_activate_r": float(t["equity"]["activate_r"]), "trail_distance_r": float(t["equity"]["distance_r"])}


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


def backtest_on(db: Session, algo: Algo, md, timeframe: str, days: int) -> dict:
    """Backtest the algo's rule on one candle size over the last `days` days (nothing saved)."""
    if not algo.parsed_rule:
        raise RuleError("no confirmed rule")
    if not algo.symbols:
        raise RuleError("add at least one symbol")
    end = utcnow()
    start = end - timedelta(days=days)
    cfg = backtest_config(db, algo.style)
    per, failed = [], []
    for sym in algo.symbols:  # one stock's missing data or a rate-limit hiccup must not sink the whole backtest
        try:
            df = md.candles(sym, timeframe, start, end)
            if df is None or len(df) < 3:
                failed.append({"symbol": sym, "reason": "no price history"})
                continue
            per.append(run(df, algo.parsed_rule, cfg, sym))
        except RuleError:
            raise
        except Exception as e:
            log.warning("backtest stock skipped", extra={"algo_id": algo.id, "stock": sym, "error": str(e)})
            failed.append({"symbol": sym, "reason": str(e)[:200]})
    if not per:
        raise RuleError("No price history could be loaded for any of the stocks: "
                        + "; ".join(f"{f['symbol']}: {f['reason']}" for f in failed[:5]))
    report = combine(per, cfg)
    report["failed_symbols"] = failed
    report["config"] = {"start": start.isoformat(), "end": end.isoformat(), "timeframe": timeframe,
                        "style": algo.style.value, "symbols": [r["symbol"] for r in per], "bars": sum(r["bars"] for r in per)}
    return report


def run_backtest(db: Session, algo: Algo, md, days: int = 365) -> dict:
    report = backtest_on(db, algo, md, algo.timeframe, days if algo.timeframe == "day" else min(days, 180))
    from syena.screener.screens import linked_screens

    if linked_screens(db, algo.id):
        report["note"] = ("These stocks are the ones passing the screen TODAY. The backtest replays the technical rule on "
                          "their past prices but cannot know which stocks passed the screen in the past (that needs "
                          "historical fundamentals), so results look better than reality would have been.")
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


# ---------------------------------------------------------------- every timeframe
# candle size -> (label, days of history tested). Short candles get short windows: a month of 1-minute candles is
# already ~7,500 bars per stock, and Angel/Kite only keep so much intraday history.
TF_WINDOWS = {"minute": ("1 min", 30), "2minute": ("2 min", 30), "5minute": ("5 min", 90), "15minute": ("15 min", 180),
              "60minute": ("1 hour", 365), "240minute": ("4 hours", 365), "day": ("1 day", 3 * 365)}
_tf_progress: dict[int, dict] = {}


def backtest_all_timeframes(db: Session, algo: Algo, md, timeframes: list[str] | None = None, max_symbols: int = 25) -> dict:
    """The same rule backtested on each candle size. Indicator periods count candles, so 'SMA 20' is 20 minutes on
    1-minute candles and 20 days on daily ones: the table shows which candle size the rule actually suits."""
    from syena.models import AppSetting

    tfs = [t for t in (timeframes or list(TF_WINDOWS)) if t in TF_WINDOWS]
    if not tfs:
        raise RuleError("pick at least one timeframe")
    syms_all = list(algo.symbols or [])
    sample = syms_all[:max_symbols]
    from types import SimpleNamespace

    shadow = SimpleNamespace(id=algo.id, parsed_rule=algo.parsed_rule, symbols=sample, style=algo.style)
    rows = []
    _tf_progress[algo.id] = {"done": 0, "total": len(tfs), "current": None}
    for k, tf in enumerate(tfs):
        label, days = TF_WINDOWS[tf]
        _tf_progress[algo.id].update(current=label)
        try:
            rep = backtest_on(db, shadow, md, tf, days)
            m = rep["metrics"]
            rows.append({"timeframe": tf, "label": label, "days": days, "metrics": m, "bars": rep["config"]["bars"],
                         "failed": len(rep["failed_symbols"]), "equity": rep["equity"][-120:]})
        except Exception as e:
            rows.append({"timeframe": tf, "label": label, "days": days, "error": str(e)[:300]})
        _tf_progress[algo.id]["done"] = k + 1
    scored = [r for r in rows if r.get("metrics") and r["metrics"]["trades"] >= 10]
    best = max(scored, key=lambda r: (r["metrics"]["profit_factor"] or 0) * min(1.0, r["metrics"]["trades"] / 30), default=None)
    out = {"at": utcnow().isoformat(), "algo_id": algo.id, "rows": rows, "best": best["timeframe"] if best else None,
           "symbols_tested": sample, "symbols_total": len(syms_all), "style": algo.style.value}
    key = f"_tf_backtest_{algo.id}"
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=out, updated_at=utcnow()))
    else:
        row.value, row.updated_at = out, utcnow()
    db.commit()
    _tf_progress.pop(algo.id, None)
    return out


def timeframe_results(db: Session, algo_id: int) -> dict | None:
    from syena.models import AppSetting

    row = db.get(AppSetting, f"_tf_backtest_{algo_id}")
    return row.value if row else None


def start(db: Session, algo: Algo) -> Algo:
    if algo.status == "DELETED":
        raise RuleError("this algo was deleted")
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
    from syena.algos.rules import strategy_frame, strategy_node

    st_node = strategy_node(rule.get("entry"))
    st = strategy_frame(df, st_node).iloc[-1] if st_node else None

    def lvl(spec, default_mult, sign, factor=1.0):
        spec = spec or {"type": "atr", "mult": default_mult}
        if spec["type"] == "strategy" and st is not None:
            planned = float(st["stop"] if sign < 0 else st["target"])
            # keep the planned price; a lesson's wider stop widens the distance from the entry
            return round(entry - (entry - planned) * factor, 2) if sign < 0 else round(planned, 2)
        if spec["type"] == "strategy":
            spec = {"type": "atr", "mult": default_mult}
        dist = entry * spec["value"] / 100 if spec["type"] == "pct" else spec["mult"] * a
        return round(entry + sign * dist * factor, 2)

    return lvl(rule.get("stop_loss"), cfg.default_stop_atr, -1, stop_factor), lvl(rule.get("target"), cfg.default_target_atr, 1)


def _strategy_why(rule: dict, df: pd.DataFrame) -> str:
    from syena.algos.rules import strategy_frame, strategy_node

    node = strategy_node(rule.get("entry"))
    if not node:
        return ""
    r = strategy_frame(df, node).iloc[-1]
    return f" | setup: {r['reason']} (planned stop {r['stop']}, target {r['target']}, 1:{r['rr']})"


def completed_bars(df: pd.DataFrame, timeframe: str, now: datetime) -> pd.DataFrame:
    """Drop the still-forming last bar so signals only use closed candles."""
    if df.empty:
        return df
    last = df.index[-1].to_pydatetime()
    if timeframe == "day":
        return df if now - last >= timedelta(hours=10) else df.iloc[:-1]  # today's daily bar closes 15:30
    end = last + timedelta(minutes=BAR_MINUTES[timeframe])
    if timeframe in ("60minute", "240minute"):  # the session's last candle ends at the 15:30 close, not a full hour/4 hours later
        from syena.market.calendar import IST

        close = datetime.combine(last.astimezone(IST).date(), datetime.min.time().replace(hour=15, minute=30), IST)
        end = min(end, close)
    return df if now >= end else df.iloc[:-1]


def tick_algo(db: Session, algo: Algo, md, now: datetime | None = None, broker_factory=None) -> list[int]:
    """Evaluate one RUNNING algo on its symbols. Returns decision ids."""
    now = now or utcnow()
    rule = algo.parsed_rule
    lookback = warmup_bars(rule) * BAR_MINUTES[algo.timeframe]
    start = now - timedelta(minutes=lookback * (3 if algo.timeframe != "day" else 2) + 3 * 1440)
    out = []
    # Positions in stocks no longer on the algo's list (e.g. they left its screen) are still managed to the end:
    # sold now if the screen says "sell when a stock leaves", otherwise by the exit rule (and stop/target as always).
    listed = set(algo.symbols or [])
    orphans = [t for t in db.scalars(select(Trade).where(Trade.algo_id == algo.id, Trade.status == TradeStatus.OPEN, Trade.instrument == "EQ"))
               if t.stock not in listed]
    if orphans:
        from syena.screener.screens import linked_screens

        sell_on_leave = any(sc.exit_on_removal for sc in linked_screens(db, algo.id))
        for t in orphans:
            df = completed_bars(md.candles(t.stock, algo.timeframe, start, now), algo.timeframe, now)
            if sell_on_leave:
                price = md.ltp([t.stock]).get(t.stock) or (float(df["close"].iloc[-1]) if len(df) else t.entry_price)
                d = engine.exit_trade(db, t, "left_screen", price, broker_factory=broker_factory,
                                      detail={"why": "stock no longer passes the linked fundamental screen"})
                out.append(d.id)
            elif len(df) >= 3 and rule.get("exit") and bool(evaluate(df, rule["exit"]).iloc[-1]):
                d = engine.exit_trade(db, t, "exit_rule", float(df["close"].iloc[-1]), broker_factory=broker_factory,
                                      detail={"rule": describe_rule(rule)})
                out.append(d.id)
    for sym in algo.symbols:
        df = completed_bars(md.candles(sym, algo.timeframe, start, now), algo.timeframe, now)
        if len(df) < 3:
            continue
        bar_ts = df.index[-1].isoformat()
        open_trade = db.scalar(select(Trade).where(Trade.algo_id == algo.id, Trade.stock == sym, Trade.status == TradeStatus.OPEN))
        opt_trade = next((t for t in db.scalars(select(Trade).where(Trade.algo_id == algo.id, Trade.instrument == "OPT",
                                                                     Trade.status.in_((TradeStatus.OPEN, TradeStatus.PENDING))))
                          if (t.option or {}).get("underlying") == sym), None)
        if opt_trade:  # option positions exit on their premium stop/target, the exit rule, or before expiry
            if opt_trade.status == TradeStatus.OPEN and rule.get("exit") and bool(evaluate(df, rule["exit"]).iloc[-1]):
                d = engine.exit_trade(db, opt_trade, "exit_rule", md.ltp([opt_trade.stock]).get(opt_trade.stock) or opt_trade.last_price,
                                      broker_factory=broker_factory, detail={"rule": describe_rule(rule)})
                out.append(d.id)
            continue
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
                     reasoning=f"rule fired on bar {bar_ts}: {describe_rule(rule).splitlines()[0]}" + _strategy_why(rule, df),
                     indicators={"snapshot": snapshot(df, rule)}, price_at_signal=price, ts=now)
        db.add(sig)
        db.flush()
        if (algo.instrument or "EQ") == "OPT":  # trade the signal with a call option instead of shares (paper)
            from syena.market.options_trade import enter_option

            d = enter_option(db, md, sym, "BUY", source="algo", algo_id=algo.id, signal_id=sig.id, confidence=100.0,
                             reasoning={"rule": describe_rule(rule), "indicators": snapshot(df, rule)},
                             inputs={"bar_ts": bar_ts, "timeframe": algo.timeframe, "underlying_price": price},
                             extra_checks=[Check("rule_signal", True, sig.reasoning)], idempotency_key=f"algo{algo.id}-{sym}-{bar_ts}-opt",
                             setup=setup, now=now, broker_factory=broker_factory)
            out.append(d.id)
            continue
        consult = pretrade.consult(db, setup, None)
        stop, target = _risk_prices(rule, df, price, algo.style, db, consult.stop_factor)
        if not stop < price < target:
            log.info("algo signal skipped: price already beyond the planned stop/target",
                     extra={"algo_id": algo.id, "stock": sym, "price": price, "stop": stop, "target": target})
            continue
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
