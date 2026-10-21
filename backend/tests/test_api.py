"""API smoke tests across pages (authenticated)."""
import time

from sqlalchemy import select

from syena.models import EvalRun


def test_dashboard(authed):
    d = authed.get("/api/dashboard").json()
    assert d["mode"]["mode"] == "PAPER" and d["wallet"]["cash"] == 100000 and d["kite"]["connected"] is False
    assert d["health"]["worker"] == "not running"


def test_settings_roundtrip_and_validation(authed):
    s = authed.get("/api/settings").json()
    assert s["values"]["sizing"] == {"min_amount": 7000.0, "max_amount": 10000.0}
    assert authed.put("/api/settings/sizing", json={"min_amount": 12000, "max_amount": 10000}).status_code == 400
    r = authed.put("/api/settings/trade_gate", json={"confidence_threshold": 75})
    assert r.status_code == 200 and r.json()["confidence_threshold"] == 75
    assert authed.put("/api/settings/holidays", json=["2026-12-25"]).json() == ["2026-12-25"]


def test_algo_parse_and_build(authed):
    r = authed.post("/api/algos/parse", json={"text": "buy when ema(50) > ema(200)", "style": "SWING", "run_mode": "PAPER"})
    assert r.json()["status"] == "ok" and "EMA(50) > EMA(200)" in r.json()["readable"]
    r = authed.post("/api/algos", json={"text": "buy when rsi < 30", "style": "INTRADAY", "run_mode": "PAPER",
                                        "symbols": ["infy"], "timeframe": "15minute"})
    assert r.status_code == 200 and r.json()["algo"]["symbols"] == ["INFY"]
    aid = r.json()["algo"]["id"]
    assert authed.post(f"/api/algos/{aid}/backtest").status_code == 409  # Zerodha not connected
    assert authed.post(f"/api/algos/{aid}/start").status_code == 400  # not backtested
    insp = authed.get(f"/api/algos/{aid}").json()
    assert insp["gate"] and not all(c["passed"] for c in insp["gate"])


def test_portfolio_empty_and_learning_pages(authed):
    p = authed.get("/api/portfolio").json()
    assert p["total_value"] == 0 and set(p["sources"]) == {"zerodha", "angel", "cas"}
    assert authed.get("/api/learning/lessons").json() == []
    assert "mistakes_by_week" in authed.get("/api/learning/charts").json()
    assert authed.get("/api/news").json() == []
    assert authed.get("/api/orders").json() == []


def test_run_parser_evals_from_ui(authed, db):
    assert authed.post("/api/evals/run", json={"suite": "parser"}).json() == {"started": "parser"}
    for _ in range(50):
        db.expire_all()
        run = db.scalar(select(EvalRun).where(EvalRun.suite == "parser"))
        if run and run.status != "RUNNING":
            break
        time.sleep(0.1)
    assert run.status == "DONE" and run.passed and run.n_cases >= 60
    summary = authed.get("/api/evals").json()
    assert summary["parser"]["latest"]["passed"] is True
    detail = authed.get(f"/api/evals/runs/{run.id}?only_failed=true").json()
    assert detail["results"] == []


def test_csv_exports(authed):
    r = authed.get("/api/export/trades.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")


def test_invest_requires_zerodha(authed):
    r = authed.post("/api/invest/review", json={"symbol": "INFY", "style": "SWING", "entry_type": "market", "entry_reason": "x",
                                               "target_pct": 12, "stop_loss_pct": 5, "holding_days": 30})
    assert r.status_code == 409


def test_delete_algo(authed, db):
    from syena.models import Algo, Signal, Style, Trade, TradeStatus
    from syena.news.pipeline import news_algo

    mk = lambda: authed.post("/api/algos", json={"text": "buy when rsi(14) < 30", "style": "SWING", "run_mode": "PAPER",  # noqa: E731
                                                 "symbols": ["INFY"], "timeframe": "day"}).json()["algo"]["id"]
    fresh = mk()
    r = authed.delete(f"/api/algos/{fresh}")
    assert r.status_code == 200 and r.json() == {"deleted": True, "kept_history": False}
    assert authed.get(f"/api/algos/{fresh}").status_code == 404

    used = mk()
    db.add(Signal(source="algo", algo_id=used, symbol="INFY", action="BUY", confidence=0))
    t = Trade(algo_id=used, source="algo", mode="PAPER", style=Style.SWING, stock="INFY", side="BUY", qty=1,
              invested_amount=1500, status=TradeStatus.OPEN)
    db.add(t)
    db.commit()
    assert authed.delete(f"/api/algos/{used}").status_code == 409  # open position
    t.status = TradeStatus.CLOSED
    db.commit()
    assert authed.delete(f"/api/algos/{used}").json()["kept_history"] is True
    assert used not in [a["id"] for a in authed.get("/api/algos").json()]
    assert authed.post(f"/api/algos/{used}/start").status_code == 400

    news_id = news_algo(db).id
    assert authed.delete(f"/api/algos/{news_id}").status_code == 400
    assert db.get(Algo, news_id) is not None
