"""Zone retest strategy (Umar Punjabi method): no look-ahead, his rules hold on every signal, and the lab / algo /
backtest / API plumbing works."""
import numpy as np
import pandas as pd
import pytest

from syena.algos import backtest, strategylab, zonetrade as zt
from syena.algos.rules import RuleError, canon_rule, describe_rule, evaluate
from syena.models import Algo
from tests.conftest import make_candles


def _df(n=900, seed=7, drift=0.0006):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.018, n)))
    o = c * np.exp(rng.normal(0, 0.006, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.008, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.008, n)))
    idx = pd.bdate_range("2021-01-04", periods=n, tz="UTC") + pd.Timedelta(hours=3, minutes=45)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": rng.integers(1e5, 1e6, n).astype(float)}, index=idx)


def test_signals_never_use_future_candles():
    df = _df(seed=3)
    full = zt.signals(df)
    fires = list(np.where(full["signal"])[0])
    assert fires, "the test series should produce some setups"
    for k in sorted(set(fires + list(range(300, 900, 53)))):
        part = zt.signals(df.iloc[:k + 1]).iloc[-1]
        f = full.iloc[k]
        assert bool(part["signal"]) == bool(f["signal"]), f"signal differs at candle {k}"
        if f["signal"]:
            assert part["stop"] == f["stop"] and part["target"] == f["target"]


@pytest.mark.parametrize("seed", range(6))
def test_every_signal_follows_his_rules(seed):
    df = _df(seed=seed)
    s = zt.signals(df)
    for ts, r in s[s["signal"]].iterrows():
        c = float(df.loc[ts, "close"]); o = float(df.loc[ts, "open"])
        assert r["bias"] == 1 and r["htf_bias"] != -1                          # buy team only
        assert r["stop"] < r["zone_low"] + 1e-9 and r["stop"] < float(df.loc[ts, "low"])  # stop beyond zone and wick
        assert float(df.loc[ts, "low"]) <= r["zone_high"]                     # price came back into the zone
        assert r["rr"] >= 2.0 - 1e-6 and r["target"] > c > r["stop"]          # at least 1:2
        assert c > o or "confirmation" in r["reason"]


def test_versions_change_what_is_traded():
    df = _df(seed=5)
    base = zt.signals(df)
    fib = zt.signals(df, {"require_fib": True})
    fixed = zt.signals(df, {"target_mode": "fixed2"})
    assert fib["signal"].sum() <= base["signal"].sum()
    assert fib[fib["signal"]]["fib"].all()
    for _, r in fixed[fixed["signal"]].iterrows():
        assert r["rr"] == pytest.approx(2.0, abs=0.02)
    with pytest.raises(ValueError):
        zt.canon_params({"target_mode": "moon"})


def test_rule_backtest_uses_the_strategy_stop_and_target():
    rule = strategylab.rule_for("base")
    assert rule["stop_loss"] == {"type": "strategy"} and rule["entry"]["op"] == "strategy"
    text = describe_rule(rule)
    assert "Zone retest" in text and "beyond the demand zone" in text and "next strong resistance" in text
    df = _df(seed=3)
    assert evaluate(df, rule["entry"]).sum() == zt.signals(df)["signal"].sum()
    res = backtest.run(df, rule, backtest.BacktestConfig(max_hold_days=30, starting_capital=1e9))
    sig = zt.signals(df)
    planned = {(round(float(r["stop"]), 2), round(float(r["target"]), 2)) for _, r in sig[sig["signal"]].iterrows()}
    assert res["trades"]
    for t in res["trades"]:
        assert (t["stop"], t["target"]) in planned


def test_strategy_stop_needs_a_strategy_entry():
    with pytest.raises(RuleError):
        canon_rule({"entry": {"op": "pattern", "name": "hammer"}, "stop_loss": {"type": "strategy"}})


def test_current_setup_explains_the_chart():
    out = zt.current_setup(_df(seed=3))
    assert out["available"] and out["bias"] and isinstance(out["recent"], list)
    assert all("retest" in r["why"] for r in out["recent"])


class MD:
    def candles(self, sym, iv, start, end):
        return make_candles(n=900, seed=sum(map(ord, sym)), vol=0.018)


def test_lab_tests_every_version_and_makes_an_algo(db):
    out = strategylab.run(db, MD(), symbols=["AAA", "BBB", "CCC"], years=3)
    assert [r["variant"] for r in out["results"]] == list(strategylab.VARIANTS)
    for row in out["results"]:
        if row["works"]:
            assert row["trades"] >= 40 and row["early"]["avg_return_pct"] > 0 and row["late"]["avg_return_pct"] > 0
    assert strategylab.latest(db)["at"] == out["at"]
    a, readable = strategylab.create_algo(db, "fixed2", ["AAA"])
    got = db.get(Algo, a.id)
    assert got.status == "DRAFT" and got.run_mode == "PAPER" and got.parsed_rule["target"] == {"type": "strategy"}
    assert "1:2" in readable


def test_strategy_lab_api(authed):
    r = authed.get("/api/pattern-lab/strategy").json()
    assert r["latest"] is None and "base" in r["variants"]
    assert authed.post("/api/pattern-lab/strategy/algo", json={"variant": "nope"}).status_code == 400
