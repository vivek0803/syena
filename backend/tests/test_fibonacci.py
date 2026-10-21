"""Swing high/low and Fibonacci indicators, checked against hand-calculated values, plus parsing."""
import pandas as pd
import pytest

from syena.algos import indicators as ind
from syena.algos.parser import parse_deterministic
from syena.algos.rules import RuleError, canon_operand, evaluate, warmup_bars


@pytest.fixture
def df():
    idx = pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC")
    return pd.DataFrame({"open": [100, 104, 108, 103, 99, 101], "high": [105, 110, 112, 106, 102, 104],
                         "low": [98, 102, 104, 100, 96, 99], "close": [104, 108, 106, 101, 100, 103],
                         "volume": [1000] * 6}, index=idx, dtype=float)


def test_swing_high_low_use_previous_bars_only(df):
    hh, ll = ind.highest(df, 3), ind.lowest(df, 3)
    assert pd.isna(hh.iloc[2])  # needs 3 completed bars before
    assert hh.iloc[3] == 112 and ll.iloc[3] == 98  # bars 0-2
    assert hh.iloc[5] == 112 and ll.iloc[5] == 96  # bars 2-4


def test_fib_levels_hand_calculated(df):
    # bar 3: range of bars 0-2 = high 112, low 98, range 14
    assert ind.fib(df, 3, 61.8).iloc[3] == pytest.approx(112 - 0.618 * 14)  # 103.348
    assert ind.fib(df, 3, 50).iloc[3] == pytest.approx(105.0)
    assert ind.fib(df, 3, 23.6).iloc[3] == pytest.approx(112 - 0.236 * 14)
    assert ind.fib(df, 3, 100).iloc[3] == pytest.approx(98.0)
    assert ind.fib_ext(df, 3, 161.8).iloc[3] == pytest.approx(98 + 1.618 * 14)  # 120.652
    assert ind.fib_ext(df, 3, 127.2).iloc[3] == pytest.approx(112 + 0.272 * 14)


def test_rule_validation_and_warmup():
    assert canon_operand({"kind": "indicator", "name": "fib", "params": {"level": 61.8}})["params"] == {"n": 50, "level": 61.8}
    with pytest.raises(RuleError):
        canon_operand({"kind": "indicator", "name": "fib", "params": {"n": 50, "level": 161.8}})
    with pytest.raises(RuleError):
        canon_operand({"kind": "indicator", "name": "fib_ext", "params": {"n": 50, "level": 61.8}})
    with pytest.raises(RuleError):
        canon_operand({"kind": "indicator", "name": "highest", "params": {}})
    r = parse_deterministic("buy when close < fib 61.8% of the last 50 candles").rule
    assert warmup_bars(r) == 50 * 3 + 10  # the level (61.8) is not a lookback


def test_golden_pocket_rule_parses_and_fires(df):
    out = parse_deterministic("Buy when close is above EMA 200 and close is between fib 50% and fib 61.8% of the last 50 candles "
                              "and stochastic %K crosses above %D and %K is below 30. Sell when close reaches the 50-candle "
                              "swing high. Stop loss 1.5 ATR")
    assert out.status == "ok"
    assert "Fib 61.8% (50-bar range)" in out.readable and "Fib 50% (50-bar range)" in out.readable
    assert "SELL when Close >= 50-bar swing high" in out.readable
    assert out.rule["stop_loss"] == {"type": "atr", "mult": 1.5, "n": 14}
    zone = parse_deterministic("buy when close is between fib 50 and fib 61.8 of the last 3 bars").rule["entry"]
    # bar 3: zone = [103.348, 105]; close 101 -> outside. bar 5: range bars 2-4 = 112/96 -> zone [102.11, 104]; close 103 -> inside
    assert list(evaluate(df, zone)) == [False, False, False, False, False, True]


@pytest.mark.parametrize("text,needle", [
    ("buy when close crosses above the 20 day high", "Close crosses above 20-bar swing high"),
    ("buy when close crosses below the 10-bar low", "Close crosses below 10-bar swing low"),
    ("buy when close > fib(100, 78.6)", "Fib 78.6% (100-bar range)"),
    ("buy when close > 38.2% fib retracement of the past 30 days", "Fib 38.2% (30-bar range)"),
    ("buy when close > fib 127.2", "Fib extension 127.2% (50-bar range)"),
    ("buy when close crosses above 161.8% extension of the last 40 bars", "Fib extension 161.8% (40-bar range)"),
])
def test_phrasings(text, needle):
    out = parse_deterministic(text)
    assert out.status == "ok", out.question
    assert needle in out.readable


@pytest.mark.parametrize("text", ["buy when close > fib", "buy when fibonacci 61.8% retracement holds",
                                  "buy at fib support", "buy when price is in the golden zone"])
def test_vague_fib_asks(text):
    assert parse_deterministic(text).status == "clarify"
