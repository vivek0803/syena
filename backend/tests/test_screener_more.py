"""Screener additions: 3-year figures and free cash flow from annual statements, FII/DII shareholding from NSE."""
from datetime import date

import pandas as pd

from syena.models import ShareholdingPattern, Stock, StockDaily
from syena.screener import data, screens, shareholding

NS = 'xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" ' \
     'xmlns:in-bse-shp="http://www.bseindia.com/xbrl/shp/2025-10-31/in-bse-shp"'


def shp_xml(symbol="INFY", day="2026-09-30", promoter=0.139, fii=0.2744, dii=0.4285, mf=0.2296):
    def ctx(cid, member):
        return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="x">500209</xbrli:identifier></xbrli:entity>'
                f'<xbrli:period><xbrli:instant>{day}</xbrli:instant></xbrli:period><xbrli:scenario>'
                f'<xbrldi:explicitMember dimension="in-bse-shp:CategoryOfShareholdersAxis">in-bse-shp:{member}</xbrldi:explicitMember>'
                f'</xbrli:scenario></xbrli:context>')

    def fact(cid, v, name="ShareholdingAsAPercentageOfTotalNumberOfShares"):
        return f'<in-bse-shp:{name} contextRef="{cid}" decimals="INF" unitRef="pure">{v}</in-bse-shp:{name}>'

    cats = [("P", "ShareholdingOfPromoterAndPromoterGroupMember", promoter), ("F", "InstitutionsForeignMember", fii),
            ("D", "InstitutionsDomesticMember", dii), ("M", "MutualFundsOrUTIMember", mf), ("PUB", "PublicShareholdingMember", 1 - promoter),
            ("S", "ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakhMember", 0.0847),
            ("L", "ResidentIndividualShareholdersHoldingNominalShareCapitalInExcessOfRsTwoLakhMember", 0.0468),
            ("ALL", "ShareholdingPatternMember", 1)]
    # a per-holder fact with two dimensions must be ignored
    two = ('<xbrli:context id="X"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity><xbrli:scenario>'
           '<xbrldi:explicitMember dimension="a">in-bse-shp:InstitutionsForeignMember</xbrldi:explicitMember>'
           '<xbrldi:explicitMember dimension="b">in-bse-shp:Holder1Member</xbrldi:explicitMember></xbrli:scenario></xbrli:context>')
    body = "".join(ctx(c, m) for c, m, _ in cats) + two + fact("X", 0.9)
    body += "".join(fact(c, v) for c, _, v in cats) + fact("ALL", 2500000, "NumberOfShareholders")
    body += f'<in-bse-shp:Symbol contextRef="MainD">{symbol}</in-bse-shp:Symbol><in-bse-shp:DateOfReport contextRef="MainI">{day}</in-bse-shp:DateOfReport>'
    return f'<?xml version="1.0"?><xbrli:xbrl {NS}>{body}</xbrli:xbrl>'.encode()


def test_parse_nse_shareholding_file():
    d = shareholding.parse_xbrl(shp_xml())
    assert d["symbol"] == "INFY" and d["as_of"] == date(2026, 9, 30)
    assert (d["promoter"], d["fii"], d["dii"], d["mf"]) == (13.9, 27.44, 42.85, 22.96)
    assert d["retail"] == 13.15 and d["shareholders"] == 2500000


def test_store_sets_holdings_and_quarter_change(db):
    db.add(Stock(symbol="INFY", name="Infosys", active=True))
    db.commit()
    shareholding.store(db, shareholding.parse_xbrl(shp_xml(day="2026-06-30", fii=0.28, dii=0.42, promoter=0.1382)), "u1")
    shareholding.store(db, shareholding.parse_xbrl(shp_xml()), "u2")
    s = db.get(Stock, "INFY")
    assert s.fii_holding == 27.44 and s.fii_change == -0.56 and s.dii_change == 0.85 and s.promoter_change == 0.08
    assert s.insider_holding == 13.9 and s.shareholding_date == date(2026, 9, 30)
    shareholding.store(db, shareholding.parse_xbrl(shp_xml()), "u2")  # same quarter again: updated, not duplicated
    assert db.query(ShareholdingPattern).count() == 2
    assert [h["as_of"] for h in shareholding.history(db, "INFY")] == ["2026-09-30", "2026-06-30"]


def test_rss_poll_reads_new_filings_only(db):
    db.add(Stock(symbol="INFY", active=True))
    db.commit()
    rss = b"<rss><channel><item><title>Infosys Limited</title><link>https://nsearchives.nseindia.com/corporate/xbrl/SHP_1_WEB.xml</link></item></channel></rss>"

    class R:
        def __init__(self, c):
            self.content = c

    calls = []

    def get(u):
        calls.append(u)
        return R(rss if u == shareholding.RSS_URL else shp_xml())

    assert shareholding.poll_rss(db, get=get)["stored"] == 1
    assert shareholding.poll_rss(db, get=get)["stored"] == 0 and calls.count("https://nsearchives.nseindia.com/corporate/xbrl/SHP_1_WEB.xml") == 1


class FakeTicker:
    cols = [pd.Timestamp(f"{y}-03-31") for y in (2026, 2025, 2024, 2023)]
    income_stmt = pd.DataFrame({c: v for c, v in zip(cols, [
        {"Total Revenue": 1331e7, "Net Income": 266e7, "Diluted EPS": 26.6, "Operating Income": 300e7, "EBIT": 310e7},
        {"Total Revenue": 1210e7, "Net Income": 230e7, "Diluted EPS": 23.0, "Operating Income": 270e7, "EBIT": 280e7},
        {"Total Revenue": 1100e7, "Net Income": 200e7, "Diluted EPS": 20.0, "Operating Income": 240e7, "EBIT": 250e7},
        {"Total Revenue": 1000e7, "Net Income": 100e7, "Diluted EPS": 10.0, "Operating Income": 200e7, "EBIT": 210e7}])})
    cashflow = pd.DataFrame({c: v for c, v in zip(cols, [
        {"Operating Cash Flow": 300e7, "Capital Expenditure": -100e7, "Free Cash Flow": 200e7},
        {"Operating Cash Flow": 260e7, "Capital Expenditure": -100e7, "Free Cash Flow": 160e7},
        {"Operating Cash Flow": 220e7, "Capital Expenditure": -100e7, "Free Cash Flow": 120e7},
        {"Operating Cash Flow": 200e7, "Capital Expenditure": -100e7, "Free Cash Flow": 100e7}])})
    balance_sheet = pd.DataFrame({c: v for c, v in zip(cols, [
        {"Stockholders Equity": 1330e7, "Total Assets": 2000e7, "Current Liabilities": 500e7}] * 4)})


def test_annual_figures_three_year_cagr_averages_and_cash_flow():
    f = data.annual_figures(FakeTicker())
    assert f["sales_cagr_3y"] == 10.0  # 1000 -> 1331 in three years
    assert f["free_cash_flow"] == 200.0 and f["capex"] == 100.0 and f["fcf_3y_avg"] == 160.0  # ₹ crore
    assert f["roe_3y_avg"] == round((266 + 230 + 200) / 1330 * 100 / 3, 2)
    assert f["opm_3y_avg"] is not None and f["roce_3y_avg"] is not None and f["profit_cagr_3y"] > 30


def test_fcf_yield_and_new_screen_fields(db):
    s = Stock(symbol="AAA", active=True, shares_outstanding=1e8, free_cash_flow=50.0, roe_3y_avg=18, sales_cagr_3y=14,
              fii_holding=25, fii_change=1.2, dii_change=0.4, promoter_change=0)
    db.add(s)
    db.commit()
    r = data.compute_ratios(s, 100.0, 120.0)  # market cap ₹1,000 cr
    assert r["fcf_yield"] == 5.0
    db.add(StockDaily(symbol="AAA", day=date(2026, 10, 27), close=100.0, fcf_yield=5.0, market_cap_cr=1000))
    db.commit()
    _, rows, _ = screens.screen_rows(db, "3 year roe > 15 and sales cagr > 12 and fcf yield > 3 and fii change > 0.5 and promoter change >= 0",
                                     date(2026, 10, 27))
    assert [x["symbol"] for x in rows] == ["AAA"]
