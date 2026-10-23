"""Proof that no indicator, candlestick pattern or support/resistance level peeks at future candles.

For every one of them: the value on candle k computed from the full history must equal the value computed when the
data stops at candle k. If anything used later candles, the two would differ and a backtest would look better
than live trading can ever be.
"""
import math

import numpy as np
import pandas as pd
import pytest

from syena.algos import patterns as pat
from syena.algos.rules import INDICATORS, operand_series

CUTS = [120, 187, 254, 333, 399]


def _df(n=400, seed=7):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.018, n)))
    o = c * np.exp(rng.normal(0, 0.006, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.008, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.008, n)))
    idx = pd.bdate_range("2023-01-02", periods=n, tz="UTC") + pd.Timedelta(hours=3, minutes=45)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": rng.integers(1e5, 1e6, n).astype(float)}, index=idx)


def _operands():
    defaults = {"sma": {"n": 20}, "ema": {"n": 20}, "wma": {"n": 20}, "highest": {"n": 20}, "lowest": {"n": 20}, "fib": {"n": 50, "level": 61.8}, "fib_ext": {"n": 50, "level": 161.8}}
    for name, spec in INDICATORS.items():
        params = {**spec["params"], **defaults.get(name, {})}
        for out in spec["outputs"] or [None]:
            yield {"kind": "indicator", "name": name, "params": params, **({"output": out} if out else {})}


def _same(a, b):
    if (a is None or (isinstance(a, float) and math.isnan(a))) and (b is None or (isinstance(b, float) and math.isnan(b))):
        return True
    return a == pytest.approx(b, rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("operand", list(_operands()), ids=lambda o: o["name"] + ("." + o["output"] if o.get("output") else ""))
def test_indicator_never_uses_future_candles(operand):
    df = _df()
    full = operand_series(df, operand)
    for k in CUTS:
        part = operand_series(df.iloc[:k], operand)
        assert _same(float(part.iloc[-1]), float(full.iloc[k - 1])), f"{operand['name']} differs at candle {k}"


@pytest.mark.parametrize("name", [n for n in pat.PATTERNS])
def test_pattern_never_uses_future_candles(name):
    df = _df()
    full = pat.detect(df, name)
    for k in CUTS:
        assert bool(pat.detect(df.iloc[:k], name).iloc[-1]) == bool(full.iloc[k - 1]), f"{name} differs at candle {k}"
