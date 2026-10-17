"""News pipeline, trade gate, replay engine (no-lookahead), signal/triage metrics."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import select

from syena import settings_store
from syena.db import utcnow
from syena.evals import metrics as M
from syena.evals import signal_eval, triage_eval
from syena.execution.brokers import PaperBroker
from syena.market.calendar import IST
from syena.market.data import store_candles
from syena.models import AICall, Decision, DecisionType, Event, NewsItem, NewsOutcome, Signal, Style, Trade, TradeStatus
from syena.news import pipeline, sources
from syena.news.context import market_context
from tests.conftest import make_candles

pf = lambda db, mode: PaperBroker()  # noqa: E731


def raw(title, mins_ago=5, src="test"):
    return sources.RawItem(src, f"https://x/{abs(hash(title))}", title, "", utcnow() - timedelta(minutes=mins_ago))


def test_ingest_dedupes_exact_and_near_duplicates(db, user):
    items = pipeline.ingest(db, [raw("Infosys wins $1.5 billion deal from European bank"),
                                 raw("Infosys wins $1.5 billion deal from European bank"),
                                 raw("Infosys bags $1.5 billion deal from European bank", src="other")])
    assert len(items) == 2 and items[1].status == "DUPLICATE" and items[1].dup_of_id == items[0].id


def test_triage_never_trusts_symbols_outside_candidates(db, user, fake_llm):
    item = pipeline.ingest(db, [raw("Infosys wins large multi-year deal")])[0]
    res = pipeline.triage(db, item, ["INFY"])
    assert res["symbols"] == ["INFY"]  # 'HACKED' dropped
    assert "<untrusted" in fake_llm.prompts[-1]


def test_triage_irrelevant_without_candidates(db, user, fake_llm):
    item = pipeline.ingest(db, [raw("Monsoon rainfall above normal")])[0]
    assert pipeline.triage(db, item, [])["relevant"] is False and not fake_llm.prompts


def test_market_context_score():
    cfg = settings_store.DEFAULTS["market_context"]
    up = market_context({"NIFTY": {"change_pct": 1.0}, "SENSEX": {"change_pct": 1.0}, "VIX": {"change_pct": -5}}, cfg)
    down = market_context({"NIFTY": {"change_pct": -2.0}, "VIX": {"change_pct": 10}}, cfg)
    assert up["score"] == pytest.approx(1.0) and down["score"] == pytest.approx(-1.0)


def _news_now(md):
    df = md.frames[("INFY", "day")]
    now = df.index[-1].to_pydatetime().astimezone(IST).replace(hour=11)
    md.as_of = now
    return now


def test_process_symbol_places_paper_trade_with_full_record(db, user, fake_llm, md):
    now = _news_now(md)
    item = NewsItem(source="t", url="u", title="Infosys wins $1.5 billion deal", published_at=now - timedelta(minutes=10), content_hash="h1")
    db.add(item)
    db.commit()
    settings_store.put(db, "trade_gate", {"require_technical_agreement": False})
    sig = pipeline.process_symbol(db, "INFY", [item], md, "order_win", pf, now=now)
    assert sig.action == "BUY" and sig.confidence == 82
    d = db.scalar(select(Decision).where(Decision.signal_id == sig.id))
    names = {c["name"] for c in d.checks_json}
    assert {"signal_action", "confidence", "market_context", "sizing"} <= names
    assert d.inputs_json["news"][0]["title"].startswith("Infosys")
    if d.type == DecisionType.PLACED:
        t = db.get(Trade, d.trade_id)
        assert t.news_item_ids == [item.id] and t.stop_loss < t.entry_price < t.target and t.auto_sell
    assert db.scalar(select(AICall)).prompt_version == "v1"


def test_low_confidence_rejected_with_exact_check(db, user, fake_llm, md):
    now = _news_now(md)
    fake_llm.analysis = {**fake_llm.analysis, "confidence": 55}
    item = NewsItem(source="t", url="u", title="Infosys deal", published_at=now, content_hash="h2")
    db.add(item)
    db.commit()
    sig = pipeline.process_symbol(db, "INFY", [item], md, None, pf, now=now)
    d = db.scalar(select(Decision).where(Decision.signal_id == sig.id))
    assert d.type == DecisionType.REJECTED and "confidence 55" in d.summary


def test_sell_signal_on_held_stock_raises_event_not_order(db, user, fake_llm, md):
    now = _news_now(md)
    db.add(Trade(source="algo", mode="PAPER", style=Style.SWING, stock="INFY", qty=6, invested_amount=9000, entry_price=1500,
                 status=TradeStatus.OPEN))
    fake_llm.analysis = {**fake_llm.analysis, "action": "SELL"}
    item = NewsItem(source="t", url="u", title="Infosys guidance cut", published_at=now, content_hash="h3")
    db.add(item)
    db.commit()
    pipeline.process_symbol(db, "INFY", [item], md, None, pf, now=now)
    assert db.scalar(select(Event).where(Event.kind == "news_sell_signal"))
    assert db.scalar(select(Decision).where(Decision.symbol == "INFY")) is None


def test_replay_data_never_returns_future_candles(db, user):
    df = make_candles(n=60)
    store_candles(db, "INFY", "day", df)
    as_of = df.index[30].to_pydatetime()
    rd = signal_eval.ReplayData(db, as_of)
    got = rd.candles("INFY", "day", df.index[0].to_pydatetime(), df.index[-1].to_pydatetime())
    assert got.index.max() <= pd.Timestamp(as_of) and len(got) == 31
    assert rd.ltp(["INFY"])["INFY"] == pytest.approx(df["close"].iloc[30])


def test_replay_pipeline_prompt_contains_no_future_prices(db, user, fake_llm):
    df = make_candles(n=300)
    df.iloc[200:, df.columns.get_loc("close")] = 99999.0  # poison the future
    store_candles(db, "INFY", "day", df)
    ts = df.index[150].to_pydatetime() + timedelta(hours=1)
    item = NewsItem(source="t", url="u", title="Infosys order win", published_at=ts, content_hash="h4")
    db.add(item)
    db.flush()
    db.add(NewsOutcome(news_item_id=item.id, symbol="INFY", ts=ts, price_at=float(df["close"].iloc[150]), ret_1d=1.2, ret_5d=2.0))
    db.commit()
    rows, cases = signal_eval.replay(db)
    assert rows and not any("99999" in p for p in fake_llm.prompts)


def test_signal_suite_flags_insufficient_data(db, user, fake_llm):
    run = signal_eval.run_suite(db, {"limit": 10})
    assert run.status == "DONE" and run.metrics["1d"]["status"] == "insufficient data" and run.passed is None


def test_metrics_calibration_and_baselines():
    rows = [{"pred": "BUY", "conf": 80, "ret_1d": 1.0, "ret_1h": 0, "ret_5d": 2} for _ in range(8)] + \
           [{"pred": "BUY", "conf": 80, "ret_1d": -1.0, "ret_1h": 0, "ret_5d": -2} for _ in range(2)]
    m = signal_eval.compute_metrics(rows, "1d", min_sample=5)
    assert m["direction_accuracy"]["value"] == 0.8 and m["status"] == "ok"
    assert m["calibration"]["ece"] == pytest.approx(0.0, abs=1e-9)
    assert m["buy"]["precision"] == 0.8 and m["baselines"]["always_buy"]["value"] == 0.8
    assert M.wilson(8, 10)[0] < 0.8 < M.wilson(8, 10)[1]


def test_triage_eval_scores_false_negatives_separately():
    cases = [({"relevant": True, "symbols": ["INFY"]}, {"relevant": False, "symbols": []}),
             ({"relevant": False, "symbols": []}, {"relevant": True, "symbols": ["TCS"]}),
             ({"relevant": True, "symbols": ["TCS"]}, {"relevant": True, "symbols": ["TCS"]})]
    m = triage_eval.score(cases)
    assert m["false_negatives_missed_material"] == 1 and m["false_positives_noise"] == 1 and m["relevance_recall"] == 0.5
