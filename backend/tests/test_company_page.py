"""screener.in-style company page: statements from Yahoo, stored per period, assembled with ratios and peers."""
from datetime import date

import pandas as pd

from syena.models import Financials, Stock, StockDaily
from syena.screener import company
from tests.test_screener_more import FakeTicker


class Ticker(FakeTicker):
    cols = FakeTicker.cols
    quarterly_income_stmt = pd.DataFrame({pd.Timestamp("2026-06-30"): {"Total Revenue": 350e7, "EBITDA": 80e7, "Net Income": 70e7, "Diluted EPS": 7.0,
                                                                        "Pretax Income": 90e7, "Tax Provision": 20e7}})


def test_statements_in_crore_with_derived_lines():
    st = company.statements(Ticker())
    q = st["Q"][date(2026, 6, 30)]
    assert q["sales"] == 350 and q["opm"] == round(80 / 350 * 100, 1) and q["eps"] == 7.0 and q["tax_pct"] == round(20 / 90 * 100, 1)
    a = st["A"][date(2026, 3, 31)]
    assert a["sales"] == 1331 and a["net_profit"] == 266
    assert st["CF"][date(2026, 3, 31)]["free_cash_flow"] == 200
    assert st["BS"][date(2026, 3, 31)]["total_assets"] == 2000


def test_company_page(db, authed):
    db.add_all([Stock(symbol="AAA", name="Aaa Ltd", industry="IT", active=True, debt_to_equity=0.05, roe_3y_avg=22, eps_ttm=26.6,
                      sales_cagr_3y=10, profit_cagr_3y=38),
                Stock(symbol="BBB", name="Bbb Ltd", industry="IT", active=True)])
    db.add(StockDaily(symbol="AAA", day=date(2026, 10, 28), close=500, pe=18.8, peg=0.5, market_cap_cr=5000))
    company.store_statements(db, "AAA", company.statements(Ticker()))
    db.commit()
    assert db.query(Financials).filter_by(symbol="AAA", kind="A").count() == 4
    p = authed.get("/api/screener/company/AAA").json()
    assert p["name"] == "Aaa Ltd" and p["summary"]["pe"] == 18.8 and p["summary"]["peg"] == 0.5
    assert p["profit_loss"]["periods"][-1] == "Mar 2026" and p["profit_loss"]["rows"][0]["label"] == "Sales"
    assert p["growth"]["sales"] == 10.0 and p["ratios"][-1]["roe"] == 20.0
    assert any("debt free" in x for x in p["pros"]) and any("profit growth" in x for x in p["pros"])
    assert {x["symbol"] for x in p["peers"]} == {"AAA", "BBB"}
    assert authed.get("/api/screener/company/NOPE").status_code == 404
    f = authed.get("/api/screener/fields").json()
    assert any(x["key"] == "peg" for x in f)
