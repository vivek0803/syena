"""Portfolio X-Ray: AMC disclosure parsing, look-through exposure, fund overlap, warnings, CAS import."""
import io
from datetime import date

import openpyxl
import pytest

from syena.models import Holding, HoldingSnapshot
from syena.portfolio import analysis, disclosures, sources

RELI, INFY, TCS, HDFC = "INE002A01018", "INE009A01021", "INE467B01029", "INE040A01034"


def workbook(sheets: dict[str, tuple[str, list[tuple]]], fractional=False) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, (scheme, rows) in sheets.items():
        ws = wb.create_sheet(sheet)
        ws.append(["Monthly Portfolio Statement as on 30 Sep 2026"])
        ws.append([scheme])
        ws.append([])
        ws.append(["Name of the Instrument", "ISIN", "Industry / Rating", "Quantity", "Market value (Rs. Lakhs)", "% to Net Assets"])
        ws.append(["Equity & Equity related"])
        for name, isin, sector, pct in rows:
            ws.append([name, isin, sector, 100, 10.0, pct / 100 if fractional else pct])
        ws.append(["Total", None, None, None, None, 100])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def add_snapshot(db, source, rows):
    s = HoldingSnapshot(source=source)
    db.add(s)
    db.flush()
    for r in rows:
        db.add(Holding(snapshot_id=s.id, source=source, **r))
    db.commit()


@pytest.fixture
def portfolio(db, user):
    add_snapshot(db, "zerodha", [
        {"asset_class": "equity", "symbol": "RELIANCE", "isin": RELI, "name": "RELIANCE", "qty": 10, "avg_price": 2400, "ltp": 3000, "value": 30000},
        {"asset_class": "equity", "symbol": "INFY", "isin": INFY, "name": "INFY", "qty": 10, "avg_price": 1500, "ltp": 1500, "value": 15000}])
    add_snapshot(db, "angel", [
        {"asset_class": "equity", "symbol": "TCS", "isin": TCS, "name": "TCS", "qty": 5, "avg_price": 3000, "ltp": 3000, "value": 15000}])
    add_snapshot(db, "cas", [
        {"asset_class": "mf", "isin": "INF000A01AA1", "name": "Alpha Flexi Cap Fund - Direct Plan - Growth", "qty": 1000, "ltp": 20, "value": 20000, "amc": "Alpha"},
        {"asset_class": "mf", "isin": "INF000B01BB2", "name": "Beta Large Cap Fund Direct Growth", "qty": 1000, "ltp": 20, "value": 20000, "amc": "Beta"}])
    content = workbook({
        "AFCF": ("Alpha Flexi Cap Fund", [("Reliance Industries Ltd", RELI, "Petroleum Products", 10), ("Infosys Ltd", INFY, "IT - Software", 8),
                                          ("HDFC Bank Ltd", HDFC, "Banks", 9), ("7.1% GOI 2034", "IN0020240019", "Sovereign", 5)]),
    })
    disclosures.import_disclosure(db, content, date(2026, 9, 30))
    content2 = workbook({"BLCF": ("Beta Large Cap Fund", [("Reliance Industries Ltd", RELI, "Petroleum Products", 9),
                                                          ("HDFC Bank Ltd", HDFC, "Banks", 10)])}, fractional=True)
    disclosures.import_disclosure(db, content2, date(2026, 9, 30))
    return db


def test_parse_workbook_handles_layout_and_fractions():
    parsed = disclosures.parse_workbook(workbook({"S1": ("Gamma Midcap Fund", [("Infosys Ltd", INFY, "IT", 4.5)])}, fractional=True))
    assert parsed[0]["scheme_name"] == "Gamma Midcap Fund"
    assert parsed[0]["holdings"][0]["pct_nav"] == pytest.approx(4.5) and parsed[0]["holdings"][0]["asset_type"] == "equity"
    assert disclosures.asset_type("IN0020240019") == "debt" and disclosures.asset_type("INE002A08534") == "debt"


def test_allocation_percentages_sum_to_100(portfolio):
    a = analysis.analyse(portfolio)
    assert a["total_value"] == 100000
    assert sum(r["pct"] for r in a["allocation"]) == pytest.approx(100, abs=0.01)
    assert a["by_source"]["zerodha"]["pct"] == 45 and a["by_asset_class"]["mf"]["pct"] == 40


def test_look_through_combines_direct_and_fund_exposure(portfolio):
    a = analysis.analyse(portfolio)
    rel = next(e for e in a["look_through"] if e["isin"] == RELI)
    # direct 30000 + 10% of 20000 (Alpha) + 9% of 20000 (Beta) = 33800
    assert rel["direct_value"] == 30000 and rel["via_funds_value"] == pytest.approx(3800) and rel["total_pct"] == pytest.approx(33.8)
    assert rel["n_funds"] == 2 and {f["scheme"] for f in rel["funds"]} == {
        "Alpha Flexi Cap Fund - Direct Plan - Growth", "Beta Large Cap Fund Direct Growth"}
    hdfc = next(e for e in a["look_through"] if e["isin"] == HDFC)
    assert hdfc["direct_value"] == 0 and hdfc["n_funds"] == 2  # held only through funds
    assert not any(e["isin"] == "IN0020240019" for e in a["look_through"])  # bonds are not stock exposure


def test_fund_overlap_and_warnings(portfolio):
    a = analysis.analyse(portfolio)
    ov = a["fund_overlap"][0]
    assert ov["common_stocks"] == 2 and ov["overlap_pct"] == pytest.approx(9 + 9)  # min(10,9) + min(9,10)
    assert any("RELIANCE" in w and "limit" in w for w in a["warnings"])


def test_missing_disclosure_reported(db, user):
    add_snapshot(db, "cas", [{"asset_class": "mf", "isin": "INF999", "name": "Unknown Fund", "qty": 1, "ltp": 100, "value": 100}])
    a = analysis.analyse(db)
    assert a["missing_disclosures"] and any("no AMC disclosure" in w for w in a["warnings"])


def test_cas_import(db, user, monkeypatch):
    import casparser

    fake = {"statement_period": {"from": "01-Apr-2026", "to": "30-Sep-2026"},
            "folios": [{"folio": "123", "amc": "Alpha", "schemes": [
                {"scheme": "Alpha Flexi Cap Fund - Direct Growth", "isin": "INF000A01AA1", "amfi": "100001", "close": 1000.5,
                 "valuation": {"nav": 20.0, "value": 20010.0}},
                {"scheme": "Closed fund", "isin": "INF0", "amfi": "1", "close": 0, "valuation": {"nav": 1, "value": 0}}]}]}
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda f, pw, output="dict": fake)
    snap = sources.import_cas(db, b"%PDF", "secret")
    rows = db.query(Holding).filter_by(snapshot_id=snap.id).all()
    assert len(rows) == 1 and rows[0].value == 20010.0 and rows[0].amfi_code == "100001"


def test_zerodha_sync_with_fake_kite(db, user):
    class K:
        def holdings(self):
            return [{"tradingsymbol": "INFY", "isin": INFY, "quantity": 4, "t1_quantity": 1, "average_price": 1400, "last_price": 1500}]

        def mf_holdings(self):
            return []

    snap = sources.sync_zerodha(db, kite=K())
    h = db.query(Holding).filter_by(snapshot_id=snap.id).one()
    assert h.qty == 5 and h.value == 7500
