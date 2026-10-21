"""Compare page: metrics per stock, technicals, partial price refresh, AI pick sanitised to the given stocks."""
from datetime import timedelta

import pytest

from syena.models import StockDaily, Watchlist
from syena.screener import compare, data, screens
from tests.conftest import make_candles
from tests.test_screener import D1, FakeMD, loaded  # noqa: F401  (fixture)


def test_metrics_keep_order_and_mark_unknown(loaded):  # noqa: F811
    rows = compare.metrics(loaded, ["LOSSCO", "GOODCO", "NOPE"])
    assert [r["symbol"] for r in rows] == ["LOSSCO", "GOODCO", "NOPE"]
    assert rows[1]["pe"] == 20.0 and rows[1]["roe"] == 20 and rows[1]["name"] == "Good Co"
    assert rows[2]["known"] is False and rows[2]["pe"] is None
    assert compare.missing_data(loaded, ["GOODCO", "NOPE"])["unknown"] == ["NOPE"]


def test_technicals():
    t = compare.technicals_from(make_candles(n=300))
    assert {"ret_1m", "ret_1y", "rsi14", "vs_dma200", "volatility", "max_drawdown"} <= set(t)
    assert t["max_drawdown"] <= 0 < t["volatility"] and 0 <= t["rsi14"] <= 100
    assert 100 <= len(t["series"]) <= 130
    short = compare.technicals_from(make_candles(n=30))
    assert short["ret_1y"] is None and short["vs_dma200"] is None and short["ret_1m"] is not None


def test_partial_refresh_does_not_hide_other_stocks_from_screens(loaded):  # noqa: F811
    day2 = D1 + timedelta(days=1)
    data.refresh_daily(loaded, FakeMD({"GOODCO": 1100, "DEBTCO": 200, "LOSSCO": 50}), day=day2, symbols=["GOODCO"])
    assert loaded.query(StockDaily).filter_by(day=day2).count() == 1
    assert screens.latest_day(loaded) == D1  # 1 of 3 refreshed: screens keep using the full day
    assert compare.metrics(loaded, ["GOODCO"])[0]["price"] == 1100  # but Compare shows the newest price


def test_ai_pick_sanitised(loaded, fake_llm):  # noqa: F811
    wl = Watchlist(name="w", symbols=["GOODCO", "DEBTCO"])
    loaded.add(wl)
    loaded.commit()
    md = FakeMD({})
    out = compare.ai_compare(loaded, wl, md, "LONG_TERM", "prefer low debt")
    assert out["top_pick"] == "DEBTCO"  # invented ticker replaced by the first ranked real one
    assert [r["symbol"] for r in out["ranking"]] == ["DEBTCO", "GOODCO"] and out["ranking"][1]["score"] == 100
    assert "ret_1y" in fake_llm.prompts[-1] and "<untrusted" in fake_llm.prompts[-1]
    assert loaded.get(Watchlist, wl.id).ai_result["top_pick"] == "DEBTCO"
    with pytest.raises(ValueError):
        compare.ai_compare(loaded, Watchlist(name="x", symbols=["GOODCO"]), md)


def test_compare_api(authed, loaded, fake_llm):  # noqa: F811
    wl = authed.post("/api/compare/lists", json={"name": "Banks", "symbols": ["goodco", "DEBTCO", "GOODCO"]}).json()
    assert wl["symbols"] == ["GOODCO", "DEBTCO"]
    det = authed.get(f"/api/compare/lists/{wl['id']}").json()
    assert [r["symbol"] for r in det["rows"]] == ["GOODCO", "DEBTCO"]
    assert authed.get("/api/compare/lists").json()[0]["count"] == 2
    authed.put(f"/api/compare/lists/{wl['id']}", json={"name": "B2", "symbols": ["LOSSCO"]})
    assert authed.post(f"/api/compare/lists/{wl['id']}/ai", json={}).status_code == 400  # one stock
    assert authed.delete(f"/api/compare/lists/{wl['id']}").json() == {"deleted": True}


def test_ai_rounds_for_big_lists(db, fake_llm, monkeypatch):
    from syena.models import Stock

    syms = [f"S{i:03d}" for i in range(90)]
    for s in syms:
        db.add(Stock(symbol=s, active=True, roe=float(int(s[1:]))))
    db.commit()
    calls = []
    def fake_ask(db_, table, horizon, note):
        calls.append([t["symbol"] for t in table])
        ranked = sorted(table, key=lambda t: -(t.get("roe") or 0))  # "AI" prefers high ROE
        return compare._clean({"top_pick": ranked[0]["symbol"], "ranking": [
            {"symbol": t["symbol"], "score": int(t["roe"]), "verdict": "good", "why": "roe"} for t in ranked]},
            [t["symbol"] for t in table]), "fake"
    monkeypatch.setattr(compare, "_ask", fake_ask)
    wl = Watchlist(name="big", symbols=syms)
    db.add(wl)
    db.commit()
    out = compare.ai_compare(db, wl, None)
    assert [len(c) for c in calls] == [40, 40, 10, 15]  # three groups, then a final of 3 x 5 winners
    assert out["top_pick"] == "S089" and out["rounds"] == 4
    assert len(out["ranking"]) == 90 and out["ranking"][0]["round"] == "final" and out["ranking"][-1]["round"] == "group"


def test_opening_big_list_fetches_only_a_few(loaded, monkeypatch):  # noqa: F811
    fetched = []

    class MD:
        def candles(self, sym, *a):
            fetched.append(sym)
            return make_candles(n=60)
    monkeypatch.setattr(compare, "LIVE_FETCH", 2)
    out = compare.technicals(MD(), ["GOODCO", "DEBTCO", "LOSSCO"], loaded, live_fetch=2)
    assert fetched == ["GOODCO", "DEBTCO"] and "Update data" in out["LOSSCO"]["error"]


def test_same_data_gives_same_answer_and_is_deterministic(loaded, fake_llm, monkeypatch):  # noqa: F811
    from syena.ai import llm

    temps = []
    real = llm.FakeBackend.complete

    def spy(self, model, system, user, max_tokens, images=None, temperature=None):
        temps.append((temperature, max_tokens))
        return real(self, model, system, user, max_tokens, images)
    monkeypatch.setattr(llm.FakeBackend, "complete", spy)
    wl = Watchlist(name="w", symbols=["GOODCO", "DEBTCO"])
    loaded.add(wl)
    loaded.commit()
    first = compare.ai_compare(loaded, wl, None)
    assert temps == [(0, 1200 + 160 * 2)] and "reused" not in first
    fake_llm.compare = {**fake_llm.compare, "top_pick": "GOODCO"}  # the AI would now say something else...
    again = compare.ai_compare(loaded, wl, None)
    assert again["reused"] and again["top_pick"] == first["top_pick"] and len(temps) == 1  # ...but nothing changed: same answer
    assert compare.ai_compare(loaded, wl, None, note="prefer low debt")["top_pick"] == "GOODCO" and len(temps) == 2
    assert "DEBTCO" in fake_llm.prompts[-1].split("GOODCO")[0]  # stocks always sent in a fixed (alphabetical) order
