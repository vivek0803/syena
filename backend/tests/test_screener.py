"""Fundamental screener: universe, daily ratios, fundamentals refresh/import, screens, change alerts, signals."""
from datetime import date

import pytest
from sqlalchemy import select

from syena.models import Algo, Screen, ScreenRun, Stock, StockDaily, Style
from syena.screener import data, screens
from tests.conftest import make_candles

D1, D2 = date(2026, 10, 15), date(2026, 10, 16)

FUND = {
    "GOODCO": dict(name="Good Co", shares_outstanding=1e8, eps_ttm=50, book_value_ps=250, dividend_ps=10, revenue_ttm=5e10,
                   ebitda_ttm=1e10, total_debt=1e9, total_cash=5e8, roe=20, debt_to_equity=0.2, sales_growth=18, profit_growth=22),
    "DEBTCO": dict(name="Debt Co", shares_outstanding=5e7, eps_ttm=10, book_value_ps=100, roe=18, debt_to_equity=2.5, sales_growth=20),
    "LOSSCO": dict(name="Loss Co", shares_outstanding=2e7, eps_ttm=-4, book_value_ps=40, roe=-5, debt_to_equity=0.1, sales_growth=-3),
}


class FakeMD:
    def __init__(self, prices):
        self.prices = prices

    def _token_map(self):
        return {s: str(i) for i, s in enumerate(FUND)}

    def daily_quotes(self, symbols):
        return {s: {"open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "prev_close": p * 0.98, "volume": 1e5,
                    "w52_high": p * 1.25, "w52_low": p * 0.7} for s, p in self.prices.items() if s in symbols}

    def candles(self, sym, interval, start, end):
        return make_candles(n=260, seed=1 if sym == "GOODCO" else 7)


@pytest.fixture
def loaded(db):
    data.sync_universe(db, list(FUND))
    data.refresh_fundamentals(db, fetch=lambda s: FUND[s], pause=0)
    data.refresh_daily(db, FakeMD({"GOODCO": 1000, "DEBTCO": 200, "LOSSCO": 50}), day=D1)
    return db


def test_ratios_from_price_and_company_figures(loaded):
    g = loaded.scalar(select(StockDaily).where(StockDaily.symbol == "GOODCO"))
    assert g.pe == 20.0 and g.pb == 4.0 and g.market_cap_cr == 10000.0  # 1000*1e8/1e7
    assert g.dividend_yield == 1.0 and g.earnings_yield == 5.0 and g.ps == 2.0
    assert g.ev_ebitda == pytest.approx((1e11 + 1e9 - 5e8) / 1e10, abs=0.01) and g.pct_from_52w_high == -20.0
    loss = loaded.scalar(select(StockDaily).where(StockDaily.symbol == "LOSSCO"))
    assert loss.pe is None and loss.earnings_yield == -8.0  # negative earnings: no P/E


def test_universe_marks_delisted_inactive(loaded):
    data.sync_universe(loaded, ["GOODCO", "DEBTCO", "NEWCO"])
    assert loaded.get(Stock, "LOSSCO").active is False and loaded.get(Stock, "NEWCO").active is True


def test_fundamentals_refresh_stalest_first_and_errors_recorded(db):
    data.sync_universe(db, ["AAA", "BBB"])
    def fetch(s):
        if s == "BBB":
            raise ValueError("no fundamentals on Yahoo")
        return {"eps_ttm": 5, "shares_outstanding": 1e6}
    out = data.refresh_fundamentals(db, fetch=fetch, pause=0)
    assert out == {"refreshed": 1, "failed": 1, "checked": 2}
    assert db.get(Stock, "BBB").fundamentals_error == "no fundamentals on Yahoo"
    assert data.refresh_fundamentals(db, fetch=fetch, pause=0)["checked"] == 0  # nothing stale for 7 days


@pytest.mark.parametrize("text,expected", [
    ("roe > 15 and debt to equity < 0.5", ["GOODCO"]),
    ("ROE above 15% and D/E < 3 and market cap > 500 cr", ["GOODCO", "DEBTCO"]),
    ("(pe < 25 or pb < 1.5) and sales growth > 0", ["DEBTCO", "GOODCO"]),
    ("not roe > 0", ["LOSSCO"]),
    ("price > 52 week low", ["DEBTCO", "GOODCO", "LOSSCO"]),
    ("P/E < 15", []),  # DEBTCO P/E 20, LOSSCO has no P/E (never passes)
])
def test_conditions(loaded, text, expected):
    _node, rows, _missing = screens.screen_rows(loaded, text)
    assert sorted(r["symbol"] for r in rows) == sorted(expected)


@pytest.mark.parametrize("bad", ["roe >", "foo > 3", "roe 15", "roe > 15 pe < 3", "(roe > 1", ""])
def test_bad_conditions_explain(bad):
    with pytest.raises(screens.ScreenError):
        screens.parse_conditions(bad)


def test_daily_run_alerts_on_added_removed_and_signals(loaded):
    from syena.algos.parser import parse_deterministic

    algo = Algo(name="screen algo", kind="rule", english_text="x", style=Style.SWING, run_mode="PAPER", status="CONFIRMED",
                symbols=["INFY"], timeframe="day")
    loaded.add(algo)
    loaded.commit()
    sc = Screen(name="Value", conditions="pe < 25 and roe > 10", alerts=True, algo_id=algo.id,
                signal_rule=parse_deterministic("buy when close > 0").rule)
    loaded.add(sc)
    loaded.commit()
    sent = []
    md = FakeMD({"GOODCO": 1000, "DEBTCO": 200, "LOSSCO": 50})
    r1 = screens.run_screen(loaded, sc, md, D1, notify=lambda db, t: sent.append(t))
    assert r1.members == ["DEBTCO", "GOODCO"] and r1.added == [] and "First run" in sent[-1]
    assert {s["symbol"] for s in r1.signals} == {"DEBTCO", "GOODCO"}
    assert loaded.get(Algo, algo.id).symbols == ["GOODCO", "DEBTCO"]  # biggest company first

    # next day GOODCO rallies to P/E 30 -> drops out; DEBTCO stays
    data.refresh_daily(loaded, FakeMD({"GOODCO": 1500, "DEBTCO": 210, "LOSSCO": 50}), day=D2)
    sc.signal_rule = None
    r2 = screens.run_screen(loaded, sc, md, D2, notify=lambda db, t: sent.append(t))
    assert r2.members == ["DEBTCO"] and r2.removed == ["GOODCO"] and r2.added == []
    assert "➖ Removed: GOODCO" in sent[-1]
    assert len(list(loaded.scalars(select(ScreenRun)))) == 2


def test_screener_csv_import(db):
    data.sync_universe(db, ["GOODCO"])
    csv_bytes = ("Name,NSE Code,Current Price,Market Capitalization,Price to Earning,Return on equity,Debt to equity,"
                 "Sales growth,EPS 12M Rs.,Book value,Promoter holding\n"
                 "Good Co,GOODCO,1000,10000,20,21.5,0.15,17,50,250,55.2\nOther,ZZZ,1,1,1,1,1,1,1,1,1\n").encode()
    out = data.import_screener_csv(db, csv_bytes)
    assert out["imported"] == 1 and out["not_in_universe"] == ["ZZZ"]
    s = db.get(Stock, "GOODCO")
    assert s.roe == 21.5 and s.debt_to_equity == 0.15 and s.eps_ttm == 50 and s.insider_holding == 55.2
    assert s.shares_outstanding == pytest.approx(1e8) and s.fundamentals_source == "csv"


def test_screener_api(authed, loaded):
    st = authed.get("/api/screener/status").json()
    assert st["stocks"] == 3 and st["with_fundamentals"] == 3 and st["latest_day"] == "2026-10-15"
    r = authed.get("/api/screener/stocks", params={"where": "roe > 15", "sort": "roe", "desc": "false"}).json()
    assert [x["symbol"] for x in r["rows"]] == ["DEBTCO", "GOODCO"]
    assert authed.get("/api/screener/stocks", params={"where": "roe >"}).status_code == 400
    body = {"name": "Quality", "conditions": "roe > 15 and debt to equity < 1", "alerts": False}
    assert authed.post("/api/screener/preview", json=body).json()["count"] == 1
    sid = authed.post("/api/screener/screens", json=body).json()["id"]
    run = authed.post(f"/api/screener/screens/{sid}/run").json()
    assert run["members"] == ["GOODCO"]
    det = authed.get(f"/api/screener/screens/{sid}").json()
    assert det["members"][0]["symbol"] == "GOODCO" and len(det["runs"]) == 1
    bad = authed.post("/api/screener/screens", json={**body, "signal_text": "when the stock looks strong"})
    assert bad.status_code == 400
    # fundamentals pick the stocks, a technical rule picks the moment
    t = authed.post(f"/api/screener/screens/{sid}/algo", json={"text": "buy when close > sma 20"}).json()
    det = authed.get(f"/api/screener/screens/{sid}").json()["screen"]
    assert det["algo_id"] == t["algo_id"] and det["exit_on_removal"] is True
    algo = authed.get(f"/api/algos/{t['algo_id']}").json()
    algo = algo.get("algo", algo)
    assert algo["symbols"] == ["GOODCO"] and algo["status"] == "DRAFT" and algo["run_mode"] == "PAPER"
    assert authed.delete(f"/api/screener/screens/{sid}").json() == {"deleted": True}


def test_linked_algo_gets_all_members_biggest_first(loaded):
    algo = Algo(name="a", english_text="x", style=Style.SWING, symbols=[], parsed_rule=None)
    loaded.add(algo)
    loaded.commit()
    sc = Screen(name="s", conditions="roe > 15", alerts=False, algo_id=algo.id)
    loaded.add(sc)
    loaded.commit()
    screens.run_screen(loaded, sc)
    assert algo.symbols == ["GOODCO", "DEBTCO"]  # no cap; GOODCO ₹10,000 cr before DEBTCO ₹1,000 cr


def test_fundamentals_stop_when_rate_limited(db, monkeypatch):
    monkeypatch.setattr(data._time, "sleep", lambda s: None)
    data.sync_universe(db, ["A1", "A2", "A3", "A4"])
    calls = []
    def fetch(s):
        calls.append(s)
        raise RuntimeError("429 Client Error: Too Many Requests")
    out = data.refresh_fundamentals(db, fetch=fetch, pause=0)
    assert len(calls) == 3 and out["refreshed"] == 0 and out["failed"] == 0
    assert all(db.get(Stock, s).fundamentals_at is None for s in ["A1", "A2", "A3", "A4"])  # retried next night


def test_new_coverage_is_not_reported_as_added(db):
    data.sync_universe(db, list(FUND))
    data.refresh_fundamentals(db, fetch=lambda s: FUND[s], pause=0, symbols=["GOODCO"])
    md = FakeMD({"GOODCO": 1000, "DEBTCO": 200, "LOSSCO": 50})
    data.refresh_daily(db, md, day=D1)
    sc = Screen(name="Q", conditions="roe > 15", alerts=False)
    db.add(sc)
    db.commit()
    assert screens.run_screen(db, sc, None, D1).members == ["GOODCO"]
    data.refresh_fundamentals(db, fetch=lambda s: FUND[s], pause=0, symbols=["DEBTCO"])  # data arrives overnight
    r = screens.run_screen(db, sc, None, D1)
    assert r.members == ["DEBTCO", "GOODCO"] and r.added == []


def test_no_prices_explains_instead_of_zero(authed, db):
    sid = authed.post("/api/screener/screens", json={"name": "PE", "conditions": "pe < 40", "alerts": False}).json()["id"]
    r = authed.post(f"/api/screener/screens/{sid}/run")
    assert r.status_code == 400 and "Update prices now" in r.json()["detail"]
    assert "Update prices now" in authed.get(f"/api/screener/screens/{sid}").json()["data_note"]
