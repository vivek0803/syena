"""P/E 0 = loss-making, and the PEG ratio."""
from datetime import date

from syena.invest import InvestPlan, deterministic_issues
from syena.models import Stock, StockDaily
from syena.screener import data, screens

SZ = {"min_amount": 7000, "max_amount": 10000}


def plan(**kw):
    base = dict(symbol="X", style="LONG_TERM", entry_type="market", entry_low=None, entry_high=None, entry_reason="r", pe=None,
                exit_condition="", target_pct=None, stop_loss_pct=None, holding_days=0)
    return InvestPlan(**{**base, **kw})


def test_pe_zero_means_loss_making_not_a_typo():
    issues = deterministic_issues(plan(pe=0), 500.0, SZ)
    pe = [i for i in issues if i["field"] == "pe"]
    assert pe and "loss-making" in pe[0]["problem"] and pe[0]["severity"] == "medium"
    assert "implausible" in [i for i in deterministic_issues(plan(pe=-4), 500.0, SZ) if i["field"] == "pe"][0]["problem"]
    assert not [i for i in deterministic_issues(plan(pe=22), 500.0, SZ) if i["field"] == "pe"]


def test_peg_uses_three_year_eps_growth():
    s = Stock(symbol="A", shares_outstanding=1e8, eps_ttm=10.0, eps_cagr_3y=20.0, profit_growth=50.0)
    r = data.compute_ratios(s, 300.0, None)
    assert r["pe"] == 30 and r["peg"] == 1.5
    assert data.compute_ratios(Stock(symbol="B", eps_ttm=-2.0, eps_cagr_3y=20.0), 100.0, None)["peg"] is None  # loss: no P/E, no PEG
    assert data.compute_ratios(Stock(symbol="C", eps_ttm=5.0, eps_cagr_3y=-5.0), 100.0, None)["peg"] is None  # shrinking earnings


def test_screens_on_peg_and_loss_making(db):
    db.add_all([Stock(symbol="GROW", active=True, eps_ttm=10), Stock(symbol="LOSS", active=True, eps_ttm=-3)])
    db.add_all([StockDaily(symbol="GROW", day=date(2026, 10, 28), close=300, pe=30, peg=0.9),
                StockDaily(symbol="LOSS", day=date(2026, 10, 28), close=50)])
    db.commit()
    assert [r["symbol"] for r in screens.screen_rows(db, "peg ratio < 1", date(2026, 10, 28))[1]] == ["GROW"]
    assert [r["symbol"] for r in screens.screen_rows(db, "loss making = 1", date(2026, 10, 28))[1]] == ["LOSS"]
