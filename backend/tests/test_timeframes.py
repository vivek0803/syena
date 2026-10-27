"""2-minute and 4-hour candles built from smaller ones, and the every-timeframe backtest."""
import numpy as np
import pandas as pd

from syena.algos import service as svc
from syena.algos.rules import canon_rule
from syena.market.data import DERIVED, resample_session
from syena.models import Algo, Style


def minutes(days=2, freq="1min"):
    frames = []
    for d in pd.bdate_range("2026-10-26", periods=days):
        idx = pd.date_range(f"{d.date()} 09:15", f"{d.date()} 15:29", freq=freq, tz="Asia/Kolkata")
        p = 100 + np.cumsum(np.random.default_rng(d.day).normal(0, 0.05, len(idx)))
        frames.append(pd.DataFrame({"open": p, "high": p + 0.1, "low": p - 0.1, "close": p + 0.02, "volume": 100.0}, index=idx))
    return pd.concat(frames).tz_convert("UTC")


def test_two_minute_candles_align_to_the_open_and_never_mix_days():
    m = minutes()
    two = resample_session(m, 2)
    ist = two.index.tz_convert("Asia/Kolkata")
    assert ist[0].strftime("%H:%M") == "09:15" and ist[1].strftime("%H:%M") == "09:17"
    assert len(two) == 2 * 188  # 375 one-minute candles a day -> 188 two-minute ones (the last one is a single minute)
    first = m.iloc[:2]
    assert two.iloc[0]["open"] == first.iloc[0]["open"] and two.iloc[0]["close"] == first.iloc[1]["close"]
    assert two.iloc[0]["high"] == first["high"].max() and two.iloc[0]["volume"] == 200


def test_four_hour_candles_are_two_per_session():
    h = resample_session(minutes(freq="60min"), 240)
    ist = h.index.tz_convert("Asia/Kolkata")
    assert [t.strftime("%H:%M") for t in ist[:2]] == ["09:15", "13:15"] and len(h) == 4
    assert DERIVED["240minute"] == ("60minute", 240)


class MD:
    def __init__(self):
        self.calls = []

    def candles(self, sym, iv, start, end):
        self.calls.append(iv)
        if iv in DERIVED:
            base, mins = DERIVED[iv]
            return resample_session(self.candles(sym, base, start, end), mins)
        freq = {"minute": "1min", "5minute": "5min", "15minute": "15min", "60minute": "60min"}.get(iv)
        if freq:
            return minutes(days=5, freq=freq)
        from tests.conftest import make_candles

        return make_candles(n=600)


def test_backtest_on_every_timeframe(db):
    rule = canon_rule({"entry": {"op": "compare", "left": {"kind": "price", "field": "close"}, "cmp": ">",
                                 "right": {"kind": "indicator", "name": "sma", "params": {"n": 20}}}})
    a = Algo(name="x", english_text="close > sma 20", style=Style.SWING, run_mode="PAPER", symbols=["AAA", "BBB"],
             timeframe="day", parsed_rule=rule, status="CONFIRMED")
    db.add(a)
    db.commit()
    md = MD()
    out = svc.backtest_all_timeframes(db, a, md)
    assert [r["timeframe"] for r in out["rows"]] == list(svc.TF_WINDOWS)
    assert all("metrics" in r for r in out["rows"]), [r.get("error") for r in out["rows"]]
    assert "2minute" in md.calls and "240minute" in md.calls
    assert svc.timeframe_results(db, a.id)["at"] == out["at"]
    assert a.backtest_result is None  # the algo's own backtest is untouched


def test_completed_bars_last_hour_of_session():
    from datetime import datetime, timezone

    idx = pd.DatetimeIndex([pd.Timestamp("2026-10-27 14:15", tz="Asia/Kolkata"), pd.Timestamp("2026-10-27 15:15", tz="Asia/Kolkata")]).tz_convert("UTC")
    df = pd.DataFrame({"open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1], "volume": [1, 1]}, index=idx)
    after_close = datetime(2026, 10, 27, 10, 1, tzinfo=timezone.utc)  # 15:31 IST: the 15:15 candle has closed
    assert len(svc.completed_bars(df, "60minute", after_close)) == 2


def test_api(authed):
    r = authed.get("/api/algos/1/backtest-timeframes")
    assert r.status_code == 404 or "timeframes" in r.json()
