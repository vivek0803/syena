"""NIFTY daily options strategy: bias, opening-range breakout, PCR filter, one trade a day, backtest."""
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from syena.market import nifty_options as nf
from syena.market.calendar import IST
from tests.conftest import make_candles

CFG = nf.DEFAULTS


def day_bars(d: date, path: list[float]) -> pd.DataFrame:
    """5-minute candles from 09:15 following `path` closes."""
    idx = pd.date_range(datetime.combine(d, time(9, 15)), periods=len(path), freq="5min", tz=IST)
    c = np.array(path, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 2, "low": np.minimum(o, c) - 2, "close": c, "volume": 1.0}, index=idx)


def test_breakout_up_buys_a_call_unless_bias_is_bearish():
    bars = day_bars(date(2026, 10, 28), [25000, 25010, 25005, 25030, 25060, 25080])
    s = nf.day_signal("neutral", bars, CFG)
    assert s["direction"] == "BUY" and s["or_high"] == 25012 and "above the opening-range high" in s["reasons"][0]
    assert nf.day_signal("bearish", bars, CFG)["direction"] is None
    assert nf.day_signal("neutral", bars, CFG, pcr=0.5)["direction"] is None  # call writers heavy


def test_breakdown_buys_a_put_and_wide_range_is_skipped():
    s = nf.day_signal("bearish", day_bars(date(2026, 10, 28), [25000, 24990, 24995, 24960, 24930]), CFG)
    assert s["direction"] == "SELL"
    wide = nf.day_signal("neutral", day_bars(date(2026, 10, 28), [25000, 25400, 25100, 25500]), CFG)
    assert wide["direction"] is None and "wide" in wide["reasons"][0]


def test_only_breakouts_inside_the_entry_window_count():
    path = [25000, 25005, 25003] + [25004] * 27 + [25100]  # the breakout comes at 11:45
    assert nf.day_signal("neutral", day_bars(date(2026, 10, 28), path), CFG)["direction"] is None


def test_daily_bias():
    up = make_candles(n=200, drift=0.004, vol=0.006)
    assert nf.daily_bias(up)[0] == "bullish"
    down = make_candles(n=200, drift=-0.004, vol=0.006)
    assert nf.daily_bias(down)[0] == "bearish"


def test_backtest_runs_and_reports():
    daily = make_candles(n=260, drift=0.001)
    frames = []
    rng = np.random.default_rng(1)
    for d in pd.bdate_range("2026-09-01", periods=30):
        frames.append(day_bars(d.date(), list(25000 + np.cumsum(rng.normal(0, 12, 75)))))
    five = pd.concat(frames).tz_convert("UTC")
    daily.index = pd.bdate_range(end="2026-08-31", periods=len(daily), tz="UTC")
    res = nf.backtest(daily, five, lot_size=75)
    assert res["days"] == 30 and res["trades"] + res["no_trade_days"] == 30
    for t in res["list"]:
        assert t["side"] in ("call", "put") and t["reason"] in ("stop", "square_off")


def test_strategy_algo_is_paper_intraday(db):
    a = nf.strategy_algo(db)
    assert a.kind == "nifty_opt" and a.instrument == "OPT" and a.run_mode == "PAPER" and nf.strategy_algo(db).id == a.id


def test_api(authed):
    r = authed.get("/api/options/nifty").json()
    assert r["config"]["lots"] == 1 and r["today"] is None


def test_run_places_one_intraday_trade_a_day(db, user):
    from datetime import timezone

    from syena.execution.brokers import PaperBroker
    from syena.models import Trade
    from tests.test_options import FakeAngel, seed

    seed(db, date(2026, 11, 3))
    today = date(2026, 10, 28)

    class MD(FakeAngel):
        def candles(self, sym, iv, start, end):
            if iv == "day":
                df = make_candles(n=200, drift=0.003, vol=0.006, price=24000)
                df.index = pd.bdate_range(end="2026-10-27", periods=200, tz="UTC") + pd.Timedelta(hours=10)
                return df
            return day_bars(today, [25000, 25010, 25005, 25030, 25060, 25080, 25090]).tz_convert("UTC")

    now = datetime(2026, 10, 28, 4, 50, tzinfo=timezone.utc)  # 10:20 IST
    pf = lambda db, mode: PaperBroker()  # noqa: E731
    st = nf.run(db, MD(), now=now, broker_factory=pf)
    assert st["direction"] == "BUY" and st["decision_id"], st
    t = db.query(Trade).one()
    assert t.instrument == "OPT" and t.stock.endswith("CE") and t.style.value == "INTRADAY" and t.option["lots"] == 1
    assert nf.run(db, MD(), now=now + timedelta(minutes=5), broker_factory=pf) == {"skipped": "already traded today"}
    assert nf.get_state(db)["days"]["2026-10-28"]["direction"] == "BUY"
