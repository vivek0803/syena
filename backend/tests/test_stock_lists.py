"""Stock pickers for algos: NSE index lists (cached), market-cap groups, sectors, screens, search."""
from datetime import timedelta

import pytest

from syena.models import AppSetting, Screen, Stock
from syena.screener import lists
from tests.test_screener import loaded  # noqa: F401  (fixture)

NIFTY_CSV = ("﻿Company Name,Industry,Symbol,Series,ISIN Code\n"
             "Good Co,IT,GOODCO,EQ,INE000A01011\nDebt Co,Metals,DEBTCO,EQ,INE000A01012\nOdd Co,Misc,ODDCO,BZ,INE000A01013\n")


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status = text, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


def test_parse_nse_constituents():
    assert lists.parse_constituents(NIFTY_CSV) == ["GOODCO", "DEBTCO"]  # BOM handled, non-equity series dropped
    with pytest.raises(lists.ListError):
        lists.parse_constituents("Name,Code\nA,1\n")


def test_index_download_falls_back_and_caches(db):
    calls = []
    def get(url):
        calls.append(url)
        return Resp("", 403) if "niftyindices" in url else Resp(NIFTY_CSV)
    assert lists.index_members(db, "nifty50", get) == ["GOODCO", "DEBTCO"]
    assert len(calls) == 2  # first site refused, the NSE archive answered
    assert lists.index_members(db, "nifty50", lambda u: Resp("", 500)) == ["GOODCO", "DEBTCO"]  # cached for a week
    row = db.get(AppSetting, "_index_nifty50")
    row.value = {**row.value, "at": (lists.utcnow() - timedelta(days=30)).isoformat()}
    db.commit()
    assert lists.index_members(db, "nifty50", lambda u: Resp("", 500)) == ["GOODCO", "DEBTCO"]  # stale beats nothing
    with pytest.raises(lists.ListError, match="Couldn't download"):
        lists.index_members(db, "bank", lambda u: Resp("", 500))


def test_cap_groups_sector_screen_and_search(loaded):  # noqa: F811
    assert lists.cap_group(loaded, "large") == ["GOODCO", "DEBTCO", "LOSSCO"]  # biggest first
    assert lists.cap_group(loaded, "mid") == []
    loaded.get(Stock, "DEBTCO").sector = "Basic Materials"
    loaded.add(Screen(name="s", conditions="roe > 15", members=["DEBTCO", "GOODCO"]))
    loaded.commit()
    assert lists.sector(loaded, "Basic Materials") == ["DEBTCO"]
    sid = str(loaded.query(Screen).one().id)
    assert lists.resolve(loaded, "screen", sid) == ["GOODCO", "DEBTCO"]
    assert lists.catalog(loaded)["sectors"] == [{"key": "Basic Materials", "label": "Basic Materials", "count": 1}]
    assert [r["symbol"] for r in lists.search(loaded, "debt")] == ["DEBTCO"]  # by company name
    assert lists.search(loaded, "GOODCO")[0] == {"symbol": "GOODCO", "name": "Good Co"}


def test_cap_group_needs_prices(db):
    with pytest.raises(lists.ListError, match="Market caps"):
        lists.cap_group(db, "large")


def test_picker_api_and_algo_stock_edit_resets_backtest(authed, loaded, monkeypatch):  # noqa: F811
    assert authed.get("/api/screener/lists").json()["max"] is None  # no limit
    assert authed.get("/api/screener/search", params={"q": "GOOD"}).json()[0]["symbol"] == "GOODCO"
    assert authed.get("/api/screener/list", params={"kind": "cap", "key": "large"}).json()["count"] == 3
    assert authed.get("/api/screener/list", params={"kind": "index", "key": "nope"}).status_code == 400
    a = authed.post("/api/algos", json={"text": "buy when close > sma 20", "style": "SWING", "run_mode": "PAPER",
                                        "symbols": ["GOODCO"], "timeframe": "day"}).json()["algo"]
    from syena.models import Algo

    algo = loaded.get(Algo, a["id"])
    algo.status, algo.backtest_passed, algo.backtest_result = "CONFIRMED", True, {"metrics": {}}
    loaded.commit()
    out = authed.patch(f"/api/algos/{a['id']}", json={"symbols": ["goodco", "DEBTCO", "GOODCO"]}).json()
    assert out["symbols"] == ["GOODCO", "DEBTCO"] and out["status"] == "DRAFT" and out["backtest_passed"] is False
    assert out["screen_linked"] is False
    big = authed.post("/api/algos", json={"text": "buy when close > sma 20", "style": "SWING", "run_mode": "PAPER",
                                          "symbols": [f"S{i}" for i in range(500)], "timeframe": "day"})
    assert big.status_code == 200 and len(big.json()["algo"]["symbols"]) == 500
