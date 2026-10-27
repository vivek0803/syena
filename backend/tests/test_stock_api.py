"""Stock page API: details (metrics, AI verdicts, decisions, trades) and chart data per range."""
import pytest

from syena.market import source
from tests.conftest import make_candles
from tests.test_screener import loaded  # noqa: F401  (fixture)


class MD:
    def candles(self, sym, interval, start, end):
        if sym == "NOPE":
            raise ValueError("'NOPE' is not an NSE equity symbol.")
        return make_candles(n=700 if interval == "day" else 300, freq="B" if interval == "day" else "5min",
                            start="2024-01-01" if interval == "day" else "2026-10-12 03:45")


def test_stock_detail_and_chart(authed, loaded, monkeypatch):  # noqa: F811
    d = authed.get("/api/stock/goodco").json()
    assert d["symbol"] == "GOODCO" and d["metrics"]["pe"] == 20.0 and d["decisions"] == [] and d["trades"] == []
    monkeypatch.setattr(source, "get_market_data", lambda db: MD())
    c = authed.get("/api/stock/GOODCO/chart", params={"range": "1Y"}).json()
    assert c["interval"] == "day" and 240 <= len(c["candles"]) <= 270
    assert set(c["lines"]) == {"sma20", "sma50", "sma200"} and len(c["lines"]["sma200"]) == len(c["candles"])  # warm-up history
    assert all(0 <= p["v"] <= 100 for p in c["rsi"])
    one = authed.get("/api/stock/GOODCO/chart", params={"range": "1D"}).json()
    assert one["interval"] == "5minute" and len({(x["t"] + 19800) // 86400 for x in one["candles"]}) == 1  # one session (Indian date)
    assert authed.get("/api/stock/GOODCO/chart", params={"range": "2W"}).status_code == 400
    assert authed.get("/api/stock/NOPE/chart").status_code == 404


def test_chart_intervals_like_tradingview(authed, loaded, monkeypatch):  # noqa: F811
    monkeypatch.setattr(source, "get_market_data", lambda db: MD())
    c = authed.get("/api/stock/GOODCO/chart", params={"range": "5D", "interval": "5minute"}).json()
    assert c["interval"] == "5minute" and set(c["lines"]) == {"ema20", "ema50", "vwap"} and "month_summary" in c
    assert {o["key"]: o["ok"] for o in c["intervals"]}["minute"] is True
    r = authed.get("/api/stock/GOODCO/chart", params={"range": "1Y", "interval": "minute"})
    assert r.status_code == 400 and "1m candles go back at most 1M" in r.json()["detail"]
    assert authed.get("/api/stock/GOODCO/chart", params={"range": "1M", "interval": "7minute"}).status_code == 400
