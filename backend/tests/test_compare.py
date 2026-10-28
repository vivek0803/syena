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


def test_ai_pick_is_formula_ranked_and_adjustments_capped(loaded, fake_llm):  # noqa: F811
    wl = Watchlist(name="w", symbols=["GOODCO", "DEBTCO"])
    loaded.add(wl)
    loaded.commit()
    md = FakeMD({})
    out = compare.ai_compare(loaded, wl, md, "LONG_TERM", "prefer low debt")
    good, debt = {r["symbol"]: r for r in out["ranking"]}["GOODCO"], {r["symbol"]: r for r in out["ranking"]}["DEBTCO"]
    assert good["formula_score"] > debt["formula_score"]          # better ROE, margins, lower debt
    assert debt["adjustment"]["points"] == 10                     # the AI asked for +25: capped at 10
    assert "FAKECO" not in {r["symbol"] for r in out["ranking"]}  # invented tickers ignored
    assert out["top_pick"] == out["ranking"][0]["symbol"] and good["why"] == "ROE 20, P/E 20"
    assert debt["why"].startswith("Within this list")             # no AI text for it: the formula's own sentence
    assert "formula_score" in fake_llm.prompts[-1] and "<untrusted" in fake_llm.prompts[-1]
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


def test_big_lists_are_ranked_in_one_go(db, fake_llm, monkeypatch):
    from syena.models import Stock

    syms = [f"S{i:03d}" for i in range(90)]
    for s_ in syms:
        db.add(Stock(symbol=s_, active=True, roe=float(int(s_[1:]))))
    db.commit()
    wl = Watchlist(name="big", symbols=syms)
    db.add(wl)
    db.commit()
    out = compare.ai_compare(db, wl, None)
    assert out["top_pick"] == "S089" and out["rounds"] == 1 and len(out["ranking"]) == 90
    assert out["ranking"][-1]["symbol"] == "S000"
    assert fake_llm.prompts[-1].count('"symbol"') == compare.AI_EXPLAIN  # only the top ones go to the AI in full


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

    def spy(self, model, system, user, max_tokens, images=None, temperature=None, web_search=0):
        temps.append(temperature)
        return real(self, model, system, user, max_tokens, images, web_search=web_search)
    monkeypatch.setattr(llm.FakeBackend, "complete", spy)
    wl = Watchlist(name="w", symbols=["GOODCO", "DEBTCO"])
    loaded.add(wl)
    loaded.commit()
    first = compare.ai_compare(loaded, wl, None)
    assert temps == [0] and "reused" not in first
    again = compare.ai_compare(loaded, wl, None)
    assert again["reused"] and again["ranking"] == first["ranking"] and len(temps) == 1


def test_formula_scoring_rules():
    from syena.screener import scoring

    rows = [{"symbol": "A", "roe": 25, "pe": 15, "sales_growth": 20, "debt_to_equity": 0.1},
            {"symbol": "B", "roe": 10, "pe": -5, "sales_growth": 5, "debt_to_equity": 2.0},
            {"symbol": "BANK", "sector": "Financial Services", "industry": "Banks", "roe": 15, "pe": 12, "debt_to_equity": 9.0}]
    sc = scoring.score(rows, "LONG_TERM")
    assert sc["A"]["score"] > sc["BANK"]["score"] > sc["B"]["score"]
    assert sc["BANK"]["breakdown"]["balance_sheet"] is None        # a bank's debt/equity isn't held against it
    assert scoring.score(rows, "LONG_TERM") == sc                  # same data, same scores
    swing = scoring.score([{"symbol": "UP", "ret_3m": 20, "rsi14": 62}, {"symbol": "DOWN", "ret_3m": -10, "rsi14": 85}], "SWING")
    assert swing["UP"]["score"] > swing["DOWN"]["score"]
