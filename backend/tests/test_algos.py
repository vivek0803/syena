"""Algo Builder: indicators, rule evaluation, parser (incl. golden-set CI gate), backtester, runner, LIVE gate."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from syena.algos import indicators as ind
from syena.algos import parser
from syena.algos import service as svc
from syena.algos.backtest import BacktestConfig, run
from syena.algos.rules import RuleError, canon_rule, evaluate
from syena.evals import parser_eval
from syena.market.calendar import IST
from syena.models import Algo, DecisionType, Style, Trade
from tests.conftest import make_candles


def test_indicator_sanity():
    df = make_candles()
    c = df["close"]
    r = ind.rsi(c, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    bb = ind.bbands(c).dropna()
    assert (bb["upper"] >= bb["middle"]).all() and (bb["middle"] >= bb["lower"]).all()
    assert ind.sma(c, 20).iloc[-1] == pytest.approx(c.iloc[-20:].mean())
    st = ind.supertrend(df).dropna()
    assert set(st["direction"].unique()) <= {1.0, -1.0}
    assert (ind.atr(df).dropna() > 0).all()
    assert ind.adx(df).dropna().between(0, 100).all()


def test_crossover_fires_only_on_the_cross_bar():
    idx = pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC")
    df = pd.DataFrame({"open": 1, "high": 1, "low": 1, "volume": 1, "close": [1, 1, 1, 1, 1, 1]}, index=idx, dtype=float)
    rule = {"op": "cross_above", "left": {"kind": "price", "field": "close"}, "right": {"kind": "value", "value": 1.5}}
    df["close"] = [1, 1, 2, 2, 1, 2]
    assert evaluate(df, rule).tolist() == [False, False, True, False, False, True]


def test_parser_golden_set_meets_ci_threshold():
    m, rows = parser_eval.evaluate_cases(parser_eval.load_cases(), lambda t: parser.parse(t, use_llm=False))
    assert m["n"] >= 60
    assert m["accuracy"] >= 0.9 and m["clarification_correct"] == 1.0, [r["case"]["id"] for r in rows if not r["ok"]]


def test_parser_never_guesses_silently(db, fake_llm):
    out = parser.parse("buy when price forms a cup and handle", db=db)
    assert out.status == "clarify"  # deterministic couldn't parse -> LLM asked -> LLM says clarify


def test_llm_rule_is_validated(db, fake_llm):
    fake_llm.parser = {"status": "ok", "entry": {"op": "compare", "left": {"kind": "indicator", "name": "ichimoku"},
                                                 "cmp": ">", "right": {"kind": "value", "value": 1}}}
    out = parser.parse("buy when the tenkan rises", db=db)
    assert out.status == "clarify" and "invalid rule" in out.question


def test_rule_validation_rejects_shorts_and_bad_params():
    with pytest.raises(RuleError):
        canon_rule({"entry": {"op": "compare", "left": {"kind": "indicator", "name": "sma", "params": {"n": 0}},
                              "cmp": ">", "right": {"kind": "value", "value": 1}}})
    with pytest.raises(RuleError):
        canon_rule({"side": "SELL", "entry": {"op": "compare", "left": {"kind": "price"}, "cmp": ">", "right": {"kind": "value", "value": 1}}})


def test_backtest_fills_next_bar_open_no_lookahead():
    df = make_candles(n=300)
    rule = parser.parse_deterministic("buy when close > sma 20, sell when close < sma 20").rule
    res = run(df, rule, BacktestConfig(slippage_bps=0))
    sig = evaluate(df, rule["entry"])
    for t in res["trades"]:
        ts = pd.Timestamp(t["entry_ts"])
        i = df.index.get_indexer([ts.tz_convert("UTC")])[0]
        assert sig.iloc[i - 1], "entry must follow a signal on the previous bar"
        assert t["entry"] == pytest.approx(df["open"].iloc[i], abs=0.01)
    m = res["metrics"]
    assert m["trades"] == len(res["trades"]) and m["total_charges"] > 0
    assert all(7000 <= t["entry"] * t["qty"] <= 10000 for t in res["trades"])


def test_backtest_intraday_squares_off_same_day():
    df = make_candles(n=400, freq="15min", start="2026-09-01 03:45")
    df = df[(df.index.tz_convert(IST).time >= datetime.strptime("09:15", "%H:%M").time())
            & (df.index.tz_convert(IST).time < datetime.strptime("15:30", "%H:%M").time())]
    rule = parser.parse_deterministic("buy when close > ema 9").rule
    res = run(df, rule, BacktestConfig(style="INTRADAY"))
    for t in res["trades"]:
        assert pd.Timestamp(t["entry_ts"]).date() == pd.Timestamp(t["exit_ts"]).date()


def test_backtest_skips_unaffordable():
    res = run(make_candles(price=15000), parser.parse_deterministic("buy when close > sma 5").rule, BacktestConfig())
    assert res["metrics"]["trades"] == 0 and res["skipped"] and "above" in res["skipped"][0]["reason"]


def test_algo_lifecycle_paper(db, user, md, monkeypatch):
    algo, out = svc.build(db, "buy when close > sma 20", Style.SWING, "PAPER", ["INFY"], "day", None)
    assert out["status"] == "ok" and algo.status == "DRAFT"
    with pytest.raises(RuleError):
        svc.start(db, algo)  # must backtest first
    monkeypatch.setattr(svc, "utcnow", lambda: md.frames[("INFY", "day")].index[-1].to_pydatetime())
    report = svc.run_backtest(db, algo, md)
    assert report["metrics"]["trades"] >= 1 and algo.status == "CONFIRMED"
    svc.start(db, algo)
    assert algo.status == "RUNNING"
    # evaluate the morning after a bar where the rule fired (only completed bars are used)
    df = md.frames[("INFY", "day")]
    sig = evaluate(df, algo.parsed_rule["entry"]).to_numpy()
    i = [k for k in range(len(df) - 1) if sig[k]][-1]
    now = df.index[i + 1].to_pydatetime().astimezone(IST).replace(hour=11)
    md.as_of = now
    from syena.execution.brokers import PaperBroker

    ids = svc.tick_algo(db, algo, md, now=now, broker_factory=lambda d, m: PaperBroker())
    assert ids
    from syena.models import Decision

    d = db.get(Decision, ids[0])
    assert d.mode == "PAPER" and d.source == "algo" and any(c["name"] == "rule_signal" for c in d.checks_json)


def test_live_gate_blocks_without_paper_history(db, user):
    algo = Algo(name="x", english_text="x", style=Style.SWING, run_mode="LIVE", parsed_rule=None,
                backtest_result={"metrics": {"trades": 50, "win_rate": 0.6, "sharpe": 1.2, "max_drawdown_pct": 5}})
    db.add(algo)
    db.commit()
    gate = {c["name"]: c["passed"] for c in svc.gate_status(db, algo)}
    assert gate["backtest_trades"] and gate["backtest_sharpe"]
    assert not gate["paper_trades"] and not gate["paper_days"]
    with pytest.raises(RuleError):
        svc.enable_live(db, algo)
