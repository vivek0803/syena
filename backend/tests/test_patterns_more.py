"""The extra candlestick patterns, pattern confirmation, and writing any pattern in an algo rule."""
import numpy as np
import pandas as pd
import pytest

from syena.algos import parser, patterns as pat
from syena.algos.rules import canon_rule, describe_rule, evaluate


def frame(bars):
    idx = pd.bdate_range("2025-01-01", periods=len(bars), tz="UTC")
    return pd.DataFrame([{"open": o, "high": h, "low": l, "close": c, "volume": 1e5} for o, h, l, c in bars], index=idx)


def falling(n=12, start=120.0, step=1.5):
    out, p = [], start
    for _ in range(n):
        out.append((p, p + 0.6, p - step - 0.6, p - step))
        p -= step
    return out, p


def rising(n=12, start=80.0, step=1.5):
    out, p = [], start
    for _ in range(n):
        out.append((p, p + step + 0.6, p - 0.6, p + step))
        p += step
    return out, p


def last(df, name):
    return bool(pat.detect(df, name).iloc[-1])


def test_bullish_kicker():
    bars, p = falling()
    bars += [(p, p + 0.3, p - 3.3, p - 3.0), (p + 0.5, p + 4.0, p + 0.4, p + 3.8)]
    assert last(frame(bars), "bullish_kicker")


def test_three_inside_up_and_down():
    bars, p = falling()
    bars += [(p, p + 0.2, p - 4.2, p - 4.0), (p - 3.5, p - 1.6, p - 3.7, p - 1.8), (p - 1.7, p + 1.0, p - 1.9, p + 0.8)]
    assert last(frame(bars), "three_inside_up") and last(frame(bars), "any_bullish")
    bars, p = rising()
    bars += [(p, p + 4.2, p - 0.2, p + 4.0), (p + 3.5, p + 3.7, p + 1.6, p + 1.8), (p + 1.7, p + 1.9, p - 1.0, p - 0.8)]
    assert last(frame(bars), "three_inside_down") and last(frame(bars), "any_bearish")


def test_inside_bar_breakout():
    bars, p = rising(6)
    bars += [(p, p + 3, p - 3, p + 1), (p + 0.5, p + 1.5, p - 1, p + 1), (p + 1, p + 4, p + 0.5, p + 3.5)]
    df = frame(bars)
    assert last(df, "inside_bar_breakout_up") and not last(df, "inside_bar_breakout_down")


def test_rising_three_methods():
    bars, p = rising(8)
    bars += [(p, p + 5.2, p - 0.2, p + 5.0), (p + 4.6, p + 4.8, p + 3.6, p + 3.8), (p + 3.8, p + 4.0, p + 2.8, p + 3.0),
             (p + 3.0, p + 3.4, p + 2.0, p + 2.4), (p + 2.6, p + 7.2, p + 2.5, p + 7.0)]
    assert last(frame(bars), "rising_three_methods")


def test_confirmation_waits_for_the_next_candle():
    bars, p = falling()
    hammer = (p - 1.0, p - 0.8, p - 6.0, p - 0.7)  # long lower wick at the low
    bars += [hammer]
    df = frame(bars)
    assert last(df, "hammer") and not bool(pat.confirmed(df, "hammer").iloc[-1])
    df2 = frame(bars + [(p - 0.6, p + 1.5, p - 1.0, p + 1.2)])  # closes above the hammer's high
    assert bool(pat.confirmed(df2, "hammer").iloc[-1])
    df3 = frame(bars + [(p - 0.6, p - 0.5, p - 2.0, p - 1.5)])  # no follow-through
    assert not bool(pat.confirmed(df3, "hammer").iloc[-1])


def test_confirmed_patterns_never_use_future_candles():
    rng = np.random.default_rng(4)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    o = c * np.exp(rng.normal(0, 0.008, 300))
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": 1e5},
                      index=pd.bdate_range("2024-01-01", periods=300, tz="UTC"))
    for name in ("hammer", "bullish_engulfing", "shooting_star", "three_inside_up"):
        full = pat.confirmed(df, name)
        for k in (60, 140, 233, 299):
            assert bool(pat.confirmed(df.iloc[:k], name).iloc[-1]) == bool(full.iloc[k - 1])


@pytest.mark.parametrize("p", [x for x in pat.catalog()], ids=lambda x: x["name"])
def test_every_pattern_can_be_written_in_a_rule(p):
    out = parser.parse(f"buy when {p['phrase']}", use_llm=False)
    assert out.status == "ok", out.as_dict()
    assert out.rule["entry"] == {"op": "pattern", "name": p["name"]}


def test_pattern_context_words():
    r = parser.parse("buy when confirmed hammer at support on high volume, sell when any bearish pattern", use_llm=False).rule
    args = r["entry"]["args"]
    assert args[0] == {"op": "pattern", "name": "hammer", "confirm": True}
    assert any(a["op"] == "compare" and a["right"].get("name") == "volume_sma" for a in args)
    assert any(a["op"] == "compare" and a["right"].get("name") == "support_zone" for a in args)
    assert r["exit"] == {"op": "pattern", "name": "any_bearish"}
    assert "confirmed by the next candle" in describe_rule(r)
    up = parser.parse("buy on morning star in an uptrend", use_llm=False)
    assert up.status == "ok" and any("50-candle" in n for n in up.notes)


def test_confirm_flag_runs_in_rules():
    bars, p = falling()
    bars += [(p - 1.0, p - 0.8, p - 6.0, p - 0.7), (p - 0.6, p + 1.5, p - 1.0, p + 1.2)]
    rule = canon_rule({"entry": {"op": "pattern", "name": "hammer", "confirm": True}})
    assert bool(evaluate(frame(bars), rule["entry"]).iloc[-1])
