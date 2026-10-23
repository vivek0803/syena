"""Support/resistance zones and market structure."""
import numpy as np
import pandas as pd

from syena.algos import levels as lv


def _bars(path, wiggle=0.004, seed=1):
    rng = np.random.default_rng(seed)
    c = np.array(path, float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, wiggle, len(c))))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, wiggle, len(c))))
    idx = pd.bdate_range("2025-01-01", periods=len(c), tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1e5}, index=idx)


def _range_market():
    # bounces between ~100 (support) and ~120 (resistance) four times, then sits in the middle
    leg = list(np.linspace(100, 120, 12)) + list(np.linspace(120, 100, 12))[1:]
    return _bars(leg * 4 + list(np.linspace(100, 110, 6)))


def test_range_finds_support_and_resistance_zones():
    a = lv.analyse(_range_market())
    s, r = a["supports"][0], a["resistances"][0]
    assert s["low"] <= 101 and s["high"] >= 99 and s["touches"] >= 3
    assert r["low"] <= 121 and r["high"] >= 119 and r["touches"] >= 3
    assert a["structure"]["trend"] == "range"


def test_uptrend_structure_and_break():
    path = []
    base = 100
    for k in range(6):  # higher highs and higher lows
        path += list(np.linspace(base, base + 12, 8)) + list(np.linspace(base + 12, base + 6, 5))[1:]
        base += 6
    st = lv.structure(_bars(path))
    assert st["trend"] == "uptrend"
    assert {"HH", "HL"} <= {x["label"] for x in st["labels"]}


def test_rolling_levels_have_no_lookahead():
    df = _range_market()
    full = lv.rolling_levels(df)
    for k in (40, 60, 75, 90):
        part = lv.rolling_levels(df.iloc[:k])
        for col in ("support", "resistance"):
            a, b = part[col].iloc[-1], full[col].iloc[k - 1]
            assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9


def test_trade_score_likes_buying_at_support():
    df = _range_market()
    at_support = df.iloc[:-6]   # ends at ~100
    buy, why = lv.score_for_trade(at_support, "BUY")
    assert buy > 0 and any("support" in w for w in why)
    sell, _ = lv.score_for_trade(at_support, "SELL")
    assert sell == -buy


def test_chart_payload_shape():
    p = lv.levels_payload(_range_market(), "day")
    assert {"supports", "resistances", "reading", "trend", "swings"} <= set(p)
    assert all(isinstance(x["t"], int) for x in p["swings"])
