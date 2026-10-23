"""Candle correctness: forming candles get corrected, splits/bonuses are adjusted, and NSE's official file check."""
import io
import zipfile
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from syena.market import data as mdata
from syena.market import verify


def _frame(rows):
    return mdata._frame(rows)


def test_saving_a_candle_again_overwrites_it(db):
    ts = datetime(2026, 10, 22, 3, 45, tzinfo=timezone.utc)
    mdata.store_candles(db, "TCS", "day", _frame([(ts, 100, 101, 99, 100.5, 1000)]))      # saved at 11:00, still forming
    mdata.store_candles(db, "TCS", "day", _frame([(ts, 100, 108, 97, 106.0, 9000)]))      # the finished candle
    got = mdata.cached_candles(db, "TCS", "day", ts - timedelta(days=1), ts + timedelta(days=1))
    assert list(got.iloc[0][["high", "low", "close", "volume"]]) == [108, 97, 106.0, 9000]


def test_recent_candles_are_downloaded_again(monkeypatch):
    mdata._tail_seen.clear()
    now = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(mdata, "utcnow", lambda: now)
    idx = pd.date_range(now - timedelta(days=30), now - timedelta(hours=2), freq="D", tz="UTC")
    cached = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)
    need = mdata.missing_ranges(cached, "TCS", "day", now - timedelta(days=30), now)
    assert need and need[-1][0] < idx[-1].to_pydatetime() and need[-1][1] == now   # today's candle is fetched again
    assert mdata.missing_ranges(cached, "TCS", "day", now - timedelta(days=30), now) == []  # not again within 5 minutes
    old_end = now - timedelta(days=10)
    assert mdata.missing_ranges(cached[cached.index <= old_end], "TCS", "day", now - timedelta(days=30), old_end) == []


def _daily(closes, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=len(closes), tz="UTC")
    c = np.array(closes, float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1000.0}, index=idx)


def test_split_is_back_adjusted():
    df = _daily([1000, 1010, 1020, 515, 520])   # 1:2 split before the 4th day
    out = mdata.adjust_corporate_actions(df)
    assert out["close"].iloc[:3].round(1).tolist() == [500.0, 505.0, 510.0]
    assert out["volume"].iloc[0] == 2000
    assert out.attrs["corporate_actions"][0]["label"].startswith("split 1:2")
    assert mdata.adjust_corporate_actions(_daily([100, 104, 99, 103])).attrs.get("corporate_actions") is None


def test_unexplained_jump_is_flagged_not_changed():
    df = _daily([100, 101, 160, 161])
    out = mdata.adjust_corporate_actions(df)
    assert out["close"].iloc[0] == 100 and out.attrs["corporate_actions"][0]["adjusted"] is False


def _bhav_zip(rows):
    head = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,TtlTradgVol\n"
    body = "".join(f"2026-10-21,2026-10-21,CM,NSE,STK,1,INE,{s},{sr},{o},{h},{l},{c},{lt},{p},{v}\n" for s, sr, o, h, l, c, lt, p, v in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bhav.csv", head + body)
    return buf.getvalue()


class R:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_verify_against_nse_bhavcopy():
    verify._cache.clear()
    files = {"20261021": _bhav_zip([("TCS", "EQ", 3000, 3050, 2990, 3041.2, 3040, 3001, 1_000_000),
                                    ("TCSX", "BE", 1, 1, 1, 1, 1, 1, 1)]),
             "20261020": _bhav_zip([("TCS", "EQ", 2980, 3010, 2970, 3001, 3002, 2975, 900_000)])}

    def get(url):
        key = url.split("_0_0_0_")[1][:8]
        return R(200, files[key]) if key in files else R(404)

    idx = pd.DatetimeIndex([datetime(2026, 10, 20, 3, 45), datetime(2026, 10, 21, 3, 45)], tz="UTC")
    ours = pd.DataFrame({"open": [2980, 3000], "high": [3010, 3050], "low": [2970, 2990], "close": [3002, 3040.0],
                         "volume": [900_000, 1_000_000]}, index=idx)
    res = verify.verify("TCS", ours, days=2, today=date(2026, 10, 22), get=get)
    assert res["ok"] and res["matched"] == 2           # closes match the last trade price, which counts
    ours.loc[idx[1], "high"] = 3200
    verify._cache.clear()
    res = verify.verify("TCS", ours, days=2, today=date(2026, 10, 22), get=get)
    assert not res["ok"] and "high 3200.00 vs NSE 3050.00" in res["days"][0]["detail"]
