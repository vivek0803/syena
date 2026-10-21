"""Candlestick patterns on hand-built candles, trendlines without look-ahead, and patterns inside algo rules."""
import numpy as np
import pandas as pd
import pytest

from syena.algos import patterns as P
from syena.algos.parser import parse_deterministic
from syena.algos.rules import RuleError, canon_rule, evaluate
from tests.conftest import make_candles


def frame(bars):
    """bars: list of (open, high, low, close). Prefixed by a trend so reversal patterns have context."""
    idx = pd.date_range("2026-01-01", periods=len(bars), freq="B", tz="Asia/Kolkata")
    return pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx).assign(volume=1000)


DOWN = [(110 - i * 2, 111 - i * 2, 107.5 - i * 2, 108 - i * 2) for i in range(8)]  # falling: closes 108 -> 94
UP = [(90 + i * 2, 92.5 + i * 2, 89 + i * 2, 92 + i * 2) for i in range(8)]  # rising: closes 92 -> 106


@pytest.mark.parametrize("name,bars", [
    ("hammer", DOWN + [(93, 93.6, 88, 93.4)]),
    ("inverted_hammer", DOWN + [(92, 97, 91.9, 92.5)]),
    ("bullish_engulfing", DOWN + [(95, 95.5, 92, 92.5), (92, 97, 91.5, 96.5)]),
    ("piercing_line", DOWN + [(96, 96.5, 92, 92.2), (91, 95, 90.5, 94.8)]),
    ("morning_star", DOWN + [(98, 98.5, 92, 92.5), (91.5, 92, 90.8, 91.6), (92, 97, 91.8, 96.5)]),
    ("shooting_star", UP + [(107, 112, 106.9, 107.4)]),
    ("hanging_man", UP + [(107, 107.5, 102, 107.4)]),
    ("bearish_engulfing", UP + [(106, 108.5, 105.8, 108), (108.5, 109, 105, 105.4)]),
    ("dark_cloud_cover", UP + [(104, 108.2, 103.8, 108), (109, 109.5, 105, 105.5)]),
    ("evening_star", UP + [(102, 108.2, 101.8, 108), (108.5, 109.3, 108.2, 108.9), (108.4, 108.6, 103, 103.5)]),
    ("three_white_soldiers", DOWN + [(94, 97.2, 93.8, 97), (96, 100.2, 95.8, 100), (99, 103.2, 98.8, 103)]),
    ("three_black_crows", UP + [(106, 106.2, 102.8, 103), (104, 104.2, 99.8, 100), (101, 101.2, 96.8, 97)]),
    ("doji", UP + [(106, 108, 104, 106.05)]),
    ("inside_bar", UP + [(104, 110, 100, 108), (105, 109, 101, 107)]),
    ("gap_up", UP + [(110, 112, 109, 111)]),
])
def test_pattern_fires_on_last_bar(name, bars):
    s = P.detect(frame(bars), name)
    assert bool(s.iloc[-1]), name
    assert not s.iloc[:-3].any() or name in ("doji", "inside_bar", "gap_up"), f"{name} fired too early"


def test_context_matters_and_any_groups():
    hammer_after_rise = frame(UP + [(107, 107.5, 102, 107.4)])
    assert not P.detect(hammer_after_rise, "hammer").iloc[-1]  # same shape after a rise is a hanging man
    assert P.detect(hammer_after_rise, "any_bearish").iloc[-1] and not P.detect(hammer_after_rise, "any_bullish").iloc[-1]
    found = P.found(frame(DOWN + [(95, 95.5, 92, 92.5), (92, 97, 91.5, 96.5)]), last=3, chart_only=True)
    assert [f["name"] for f in found] == ["bullish_engulfing"]


def test_trendline_has_no_lookahead():
    df = make_candles(n=300, seed=4)
    full = P.trendline(df, 50, "support")
    part = P.trendline(df.iloc[:200], 50, "support")
    pd.testing.assert_series_equal(full.iloc[:200], part, check_names=False)  # adding future bars changes nothing
    assert full.notna().sum() > 200
    segs = P.trendline_segments(df, 60)
    assert {s["kind"] for s in segs} <= {"support", "resistance"} and all(len(s["points"]) == 3 for s in segs)


def test_patterns_and_trendlines_in_rules():
    df = make_candles(n=400, seed=2)
    rule = canon_rule({"entry": {"op": "and", "args": [{"op": "pattern", "name": "any_bullish"},
                                                        {"op": "compare", "left": {"kind": "price", "field": "close"}, "cmp": ">",
                                                         "right": {"kind": "indicator", "name": "trendline_support", "params": {"n": 60}}}]},
                       "exit": None, "side": "BUY"})
    sig = evaluate(df, rule["entry"])
    assert sig.dtype == bool and sig.any()
    with pytest.raises(RuleError, match="unknown candlestick pattern"):
        canon_rule({"entry": {"op": "pattern", "name": "unicorn"}, "side": "BUY"})
    r = parse_deterministic("buy when bullish engulfing and close crosses above trendline resistance, stop loss 2%")
    assert r.status == "ok" and "Bullish engulfing" in r.readable and "Trendline resistance" in r.readable


def test_month_summary_and_multi_timeframe():
    from datetime import timedelta

    from syena.news.context import multi_timeframe

    df = make_candles(n=400, seed=5)
    s = P.summary(df, since=df.index[-1] - timedelta(days=30))
    assert s["available"] and s["bars"] <= 23 and s["bias"] in ("bullish", "bearish", "mixed") and "trendline" in s["trendline"] or s["trendline"] == "no clear trendline"

    class MD:
        def candles(self, sym, tf, start, end):
            if tf == "minute":
                raise RuntimeError("Angel One: too many requests")
            return make_candles(n=900, freq="B" if tf == "day" else "15min", start="2025-01-01" if tf == "day" else "2026-09-01 03:45")

    out = multi_timeframe(MD(), "INFY", make_candles(n=900, freq="15min", start="2026-09-01 03:45").index[-1].to_pydatetime(),
                          ["day", "15minute", "minute"], days=30)
    assert [o["timeframe"] for o in out] == ["daily", "15-minute", "1-minute"]
    assert out[1]["available"] and out[1]["bars"] > 100
    assert out[2]["available"] is False and "too many" in out[2]["error"]  # one failing timeframe never breaks analysis
