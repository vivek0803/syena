from syena.invest import InvestPlan, deterministic_issues, entry_ref, levels

SIZING = {"min_amount": 1000, "max_amount": 100000}


def plan(**kw):
    base = dict(symbol="TCS", style="SWING", entry_type="market", entry_low=None, entry_high=None, entry_reason="x",
                pe=None, exit_condition="", target_pct=10, stop_loss_pct=5, holding_days=30)
    base.update(kw)
    return InvestPlan(**base)


def test_levels_from_percent():
    assert levels(plan(), 1000) == (1100.0, 950.0)
    assert levels(plan(target_pct=None, stop_loss_pct=None, style="LONG_TERM"), 1000) == (None, None)
    t, s = levels(plan(target_pct=7.3, stop_loss_pct=3.1), 1234.55)
    assert round(t / 0.05, 6) == round(t / 0.05) and t > 1234.55 > s


def test_entry_ref_uses_range_top():
    p = plan(entry_type="range", entry_low=900, entry_high=980)
    assert entry_ref(p, 1000) == 980 and entry_ref(p, 950) == 950


def test_swing_needs_target_and_stop_but_long_term_does_not():
    fields = {i["field"] for i in deterministic_issues(plan(target_pct=None, stop_loss_pct=None), 1000, SIZING) if i["severity"] == "high"}
    assert fields == {"target_pct", "stop_loss_pct"}
    lt = deterministic_issues(plan(style="LONG_TERM", target_pct=None, stop_loss_pct=None, holding_days=0), 1000, SIZING)
    assert not [i for i in lt if i["severity"] == "high"]


def test_reward_risk_and_style_warnings():
    probs = {i["field"]: i for i in deterministic_issues(plan(target_pct=3, stop_loss_pct=6), 1000, SIZING)}
    assert "reward:risk" in probs["target_pct"]["problem"]
    intr = deterministic_issues(plan(style="INTRADAY", target_pct=15, stop_loss_pct=2, holding_days=0), 1000, SIZING)
    assert any("one day" in i["problem"] for i in intr)
    lt = deterministic_issues(plan(style="LONG_TERM", stop_loss_pct=3, holding_days=30), 1000, SIZING)
    assert {i["field"] for i in lt} >= {"stop_loss_pct", "holding_days"}


def test_review_and_confirm_long_term_with_percent(db, md, monkeypatch):
    from datetime import datetime

    from syena import invest
    from syena.execution.brokers import PaperBroker
    from syena.market.calendar import IST
    from syena.models import DecisionType, Style, Trade

    t_open = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
    monkeypatch.setattr(invest, "utcnow", lambda: t_open)
    md.as_of = t_open
    p = plan(symbol="TCS", style="LONG_TERM", target_pct=40, stop_loss_pct=15, holding_days=365)
    r = invest.review(db, p, md)
    ltp = r["ltp"]
    assert r["levels"]["target"] == round(round(ltp * 1.4 / 0.05) * 0.05, 2)
    assert r["levels"]["stop_loss"] < ltp < r["levels"]["target"]
    d = invest.confirm(db, r["review_id"], p, md, lambda db, mode: PaperBroker())
    assert d.type == DecisionType.PLACED, d.summary
    t = db.get(Trade, d.trade_id)
    assert t.style == Style.LONG_TERM
    assert abs(t.target / ltp - 1.40) < 0.001 and abs(t.stop_loss / ltp - 0.85) < 0.001
