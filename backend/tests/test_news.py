"""News pipeline, trade gate, replay engine (no-lookahead), signal/triage metrics."""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import func, select

from syena import settings_store
from syena.db import utcnow
from syena.evals import metrics as M
from syena.evals import signal_eval, triage_eval
from syena.execution.brokers import PaperBroker
from syena.market.calendar import IST
from syena.market.data import store_candles
from syena.models import AICall, Decision, DecisionType, Event, NewsAnalysis, NewsItem, NewsOutcome, Order, Signal, Style, Trade, TradeStatus
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
    # no name matched: one cheap AI lookup for brand names; it found no listed company, so no triage call
    assert pipeline.triage(db, item, [])["relevant"] is False and len(fake_llm.prompts) == 1


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
    pass
    sig = pipeline.process_symbol(db, "INFY", [item], md, "order_win", pf, now=now)
    assert sig.action == "BUY" and sig.confidence == 82
    d = db.scalar(select(Decision).where(Decision.signal_id == sig.id))
    names = {c["name"] for c in d.checks_json}
    assert {"signal_action", "confidence", "market_context", "sizing"} <= names
    assert d.inputs_json["news"][0]["title"].startswith("Infosys")
    if d.type == DecisionType.PLACED:
        t = db.get(Trade, d.trade_id)
        assert t.news_item_ids == [item.id] and t.stop_loss < t.entry_price < t.target and t.auto_sell
    assert db.scalar(select(AICall)).prompt_version == "v6"


def test_low_confidence_rejected_with_exact_check(db, user, fake_llm, md):
    now = _news_now(md)
    fake_llm.analysis = {**fake_llm.analysis, "confidence": 55}
    item = NewsItem(source="t", url="u", title="Infosys deal", published_at=now, content_hash="h2")
    db.add(item)
    db.commit()
    sig = pipeline.process_symbol(db, "INFY", [item], md, None, pf, now=now)
    d = db.scalar(select(Decision).where(Decision.signal_id == sig.id))
    assert d.type == DecisionType.REJECTED and "news 55×80%" in d.summary and "weighted score" in d.summary


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


def test_run_cycle_retries_triaged_item_whose_analysis_failed(db, user, fake_llm, md):
    from syena.models import NewsAnalysis

    item = pipeline.ingest(db, [raw("Infosys wins large multi-year deal")])[0]
    item.status = "TRIAGED"  # triage succeeded earlier, analysis failed (e.g. no price source then)
    db.add(NewsAnalysis(news_item_id=item.id, stage="triage", relevant=True, symbols=["INFY"], event_type="order_win"))
    db.commit()
    stats = pipeline.run_cycle(db, md, fetch=lambda feeds: [], broker_factory=pf)
    assert stats["retried"] == 1 and stats["signals"] == 1
    db.refresh(item)
    assert item.status == "ANALYSED"
    assert db.scalar(select(Decision).where(Decision.symbol == "INFY")) is not None


def test_run_cycle_gives_up_after_one_retry(db, user, fake_llm):
    from syena.models import NewsAnalysis

    class BrokenMD:
        def candles(self, *a, **k):
            raise RuntimeError("no prices")

    item = pipeline.ingest(db, [raw("Infosys wins large multi-year deal")])[0]
    item.status = "TRIAGED"
    db.add(NewsAnalysis(news_item_id=item.id, stage="triage", relevant=True, symbols=["INFY"]))
    db.commit()
    stats = pipeline.run_cycle(db, BrokenMD(), fetch=lambda feeds: [])
    db.refresh(item)
    assert stats["errors"] == 1 and item.status == "FAILED"


def test_hold_verdict_is_recorded_as_hold_and_funnel(authed, db, md, fake_llm, monkeypatch):
    """Simple outcomes: a HOLD verdict is REJECTED with that reason, and the funnel counts it as rejected."""
    from datetime import timedelta

    from syena.models import Decision, DecisionType, NewsItem
    from syena.news import pipeline

    fake_llm.analysis = {**fake_llm.analysis, "action": "HOLD", "confidence": 40}
    now = md.frames[("INFY", "day")].index[-1].to_pydatetime()
    item = NewsItem(source="t", url="u", title="Infosys routine update", body="", published_at=now - timedelta(minutes=1),
                    content_hash="h-hold", status="TRIAGED")
    db.add(item)
    db.commit()
    md.as_of = now
    pipeline.process_symbol(db, "INFY", [item], md, "other", now=now)
    d = db.query(Decision).order_by(Decision.id.desc()).first()
    assert d.type == DecisionType.REJECTED and d.summary.startswith("REJECTED: AI verdict HOLD")
    assert d.checks_json[0]["name"] == "ai_verdict" and not d.checks_json[0]["passed"]
    from syena.models import Signal

    for row in [*db.query(Signal).all(), d]:  # the test clock is in the past: move records into the window
        row.ts = utcnow()
    item.fetched_at = utcnow()
    db.commit()
    f = authed.get("/api/news/funnel", params={"hours": 24}).json()
    assert f["verdicts"].get("HOLD") == 1 and f["rejected"] == 1 and f["blocked_by"] == {"ai_verdict": 1}
    assert [x["id"] for x in authed.get("/api/decisions", params={"type": "rejected"}).json()] == [d.id]


def test_series_suffix_maps_to_plain_symbol(db):
    from syena.models import Instrument
    from syena.news import mapping

    db.add_all([Instrument(instrument_token=1, tradingsymbol="GULFPETRO-BE", name="GP PETROLEUMS", exchange="NSE",
                           instrument_type="EQ", updated_on=date.today()),
                Instrument(instrument_token=2, tradingsymbol="SICKCO-BZ", name="SICK COMPANY", exchange="NSE",
                           instrument_type="EQ", updated_on=date.today()),
                Instrument(instrument_token=3, tradingsymbol="BAJAJ-AUTO", name="BAJAJ AUTO", exchange="NSE",
                           instrument_type="EQ", updated_on=date.today())])
    db.commit()
    c = mapping.candidates(db, "GP Petroleums wins order; Sick Company update; Bajaj Auto sales")
    assert "GULFPETRO" in c and "GULFPETRO-BE" not in c and "BAJAJ-AUTO" in c
    assert not any(k.startswith("SICKCO") for k in c)


def test_sell_verdict_opens_intraday_short_and_covers(db, user, fake_llm, md):
    """SELL on a stock you don't hold: sell short intraday (MIS), stop above / target below, bought back on exit."""
    from syena.execution.engine import exit_trade
    from syena.execution.brokers import get_wallet

    now = _news_now(md)
    pass
    fake_llm.analysis = {**fake_llm.analysis, "action": "SELL", "confidence": 95}
    item = NewsItem(source="t", url="u", title="Infosys guidance cut sharply", published_at=now, content_hash="h-short")
    db.add(item)
    db.commit()
    cash0 = get_wallet(db).cash
    sig = pipeline.process_symbol(db, "INFY", [item], md, None, pf, now=now)
    d = db.scalar(select(Decision).where(Decision.signal_id == sig.id))
    assert d.type == DecisionType.PLACED, d.summary
    t = db.get(Trade, d.trade_id)
    assert t.side == "SELL" and t.style == Style.INTRADAY and t.target < t.entry_price < t.stop_loss
    o = db.scalar(select(Order).where(Order.trade_id == t.id))
    assert o.product == "MIS" and o.side == "SELL"
    exit_trade(db, t, "target", round(t.entry_price * 0.98, 2), broker_factory=pf)
    assert t.status == TradeStatus.CLOSED and t.profit_loss > 0  # price fell: the short made money
    assert get_wallet(db).cash == pytest.approx(cash0 + t.profit_loss, abs=0.05)  # margin released + profit


def test_short_selling_off_and_errors_are_rejected_with_reason(db, user, fake_llm, md):
    from syena.models import Decision as D

    now = _news_now(md)
    settings_store.put(db, "news", {"short_selling": False})
    fake_llm.analysis = {**fake_llm.analysis, "action": "SELL"}
    item = NewsItem(source="t", url="u", title="Infosys probe", published_at=now, content_hash="h-off")
    db.add(item)
    db.commit()
    sig = pipeline.process_symbol(db, "INFY", [item], md, None, pf, now=now)
    d = db.scalar(select(D).where(D.signal_id == sig.id))
    assert d.type == DecisionType.REJECTED and "short selling is off" in d.summary
    pipeline.reject_error(db, "GHOST", [item], "analysis", ValueError("'GHOST' is not an NSE equity symbol."))
    r = db.scalar(select(D).where(D.symbol == "GHOST"))
    assert r.type == DecisionType.ERROR and r.summary.startswith("ERROR at analysis: no price data")
    assert r.checks_json[0] == {**r.checks_json[0], "name": "analysis", "passed": False}


def test_fair_queue_and_routine_filings_and_template_dedupe(db, user):
    """A flood of NSE filings can't starve other sources; routine filings never reach the AI; template look-alike
    Telegram tips about different stocks are not duplicates of each other."""
    from syena.news import sources as S

    now = utcnow()
    raw = [S.RawItem("NSE announcements", f"u{i}", f"Company{i} Ltd - Outcome of Board Meeting", "", now - timedelta(minutes=1))
           for i in range(30)]
    raw += [S.RawItem("NSE announcements", "r1", "ABC Ltd - Closure of Trading Window", "", now),
            S.RawItem("Moneycontrol latest", "m1", "Tata Motors shares jump after JLR sales beat", "", now - timedelta(minutes=2)),
            S.RawItem("Telegram @x", "t1", "TATAMOTORS buy above 900 target 950 sl 880", "TATAMOTORS buy above 900 target 950 sl 880", now, key="tg:x/1"),
            S.RawItem("Telegram @x", "t2", "HDFCBANK buy above 1600 target 1650 sl 1580", "HDFCBANK buy above 1600 target 1650 sl 1580", now, key="tg:x/2")]
    items = pipeline.ingest(db, raw, now=now)
    st = {i.url: i.status for i in items}
    assert st["r1"] == "IRRELEVANT" and st["t1"] == st["t2"] == "NEW"  # not duplicates of each other
    q = pipeline.fair_queue([i for i in items if i.status == "NEW"], 6)
    assert {i.source for i in q} == {"NSE announcements", "Moneycontrol latest", "Telegram @x"}


def test_nse_date_format_and_old_default_feeds_upgrade(db):
    from syena.news import sources as S

    d = S.parse_date({"published": "19-Oct-2026 20:45:12"})
    assert d.isoformat() == "2026-10-19T15:15:12+00:00"  # IST -> UTC
    settings_store.put(db, "news", {"feeds": [
        {"name": "NSE announcements", "url": "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"},
        {"name": "Moneycontrol markets", "url": "https://www.moneycontrol.com/rss/marketreports.xml"},
        {"name": "Economic Times markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
        {"name": "Livemint markets", "url": "https://www.livemint.com/rss/markets"}]})
    assert len(settings_store.get(db, "news")["feeds"]) == len(settings_store.DEFAULT_FEEDS) > 4
    settings_store.put(db, "news", {"feeds": [{"name": "Mine", "url": "https://x.test/rss"}]})
    assert settings_store.get(db, "news")["feeds"] == [{"name": "Mine", "url": "https://x.test/rss"}]  # your own list is kept


def test_small_company_rejected_by_liquidity_and_reward_risk_recorded(db, user, fake_llm, md):
    from datetime import date as _date

    from syena.models import Stock, StockDaily

    now = _news_now(md)
    pass
    db.add(Stock(symbol="INFY", active=True))
    db.add(StockDaily(symbol="INFY", day=_date(2026, 10, 15), close=1500, market_cap_cr=300))
    item = NewsItem(source="t", url="u", title="Infosys wins order", published_at=now, content_hash="h-liq")
    db.add(item)
    db.commit()
    sig = pipeline.process_symbol(db, "INFY", [item], md, "order_win", pf, now=now)
    d = db.scalar(select(Decision).where(Decision.signal_id == sig.id))
    checks = {c["name"]: c for c in d.checks_json}
    assert d.type == DecisionType.REJECTED and "liquidity" in d.summary and "market cap ₹300 cr" in checks["liquidity"]["detail"]
    assert checks["reward_risk"]["passed"] and "reward" in checks["reward_risk"]["detail"]


def test_weighted_score_80_15_5(db):
    a = {"action": "BUY", "technicals": {"available": True, "score": 3}, "context": {"available": True, "score": 1.0}}
    score, br = pipeline.weighted_score(db, a, 70)
    assert br["parts"] == {"news": 70.0, "technicals": 100.0, "market": 100.0} and score == 76.0  # 56 + 15 + 5
    short = {**a, "action": "SELL"}  # the same bullish technicals and market count against a short
    assert pipeline.weighted_score(db, short, 70)[0] == 56.0
    unknown = {"action": "BUY", "technicals": {"available": False}, "context": {"available": False}}
    assert pipeline.weighted_score(db, unknown, 90)[0] == 82.0  # 72 + 7.5 + 2.5 (neutral 50s)
    settings_store.put(db, "trade_gate", {"weights": {"news": 100, "technicals": 0, "market": 0}})
    assert pipeline.weighted_score(db, a, 70)[0] == 70.0


def test_filing_pdf_is_read_before_analysis(db, user, fake_llm, md, monkeypatch):
    """NSE filings arrive as a headline only: the attached PDF's text is added before the AI analyses it."""
    from syena.news import sources as S

    now = _news_now(md)
    item = NewsItem(source="NSE announcements", url="https://nsearchives.nseindia.com/corporate/INFY_123.pdf",
                    title="Infosys Limited - Award of Order", body="", published_at=now, content_hash="h-pdf", status="TRIAGED")
    db.add(item)
    db.commit()
    db.add(NewsAnalysis(news_item_id=item.id, stage="triage", relevant=True, symbols=["INFY"], event_type="order_win"))
    db.commit()
    monkeypatch.setattr(pipeline, "max_age", lambda db: timedelta(days=3650))
    pipeline.run_cycle(db, md, fetch=lambda feeds: [], fetch_telegram=lambda c: [], broker_factory=pf, now=now,
                       filing_get=lambda url: "Order value Rs 1,250 crore from a European bank over 5 years.")
    db.refresh(item)
    assert S.FILING_MARK in item.body and "1,250 crore" in item.body
    assert "1,250 crore" in fake_llm.prompts[-1]  # the AI saw the figures


def test_pdf_text_and_skip_sme_symbols(db):
    from syena.models import Instrument, Stock
    from syena.news import mapping, sources as S

    assert mapping.base_symbol("AVANA-SM") is None and mapping.base_symbol("RICHA-ST") is None
    db.add_all([Stock(symbol="INFY", active=True, name="Infosys Limited"),
                Instrument(instrument_token=9, tradingsymbol="NOPRICE", name="NO PRICE INDUSTRIES", exchange="NSE",
                           instrument_type="EQ", updated_on=date.today())])
    db.commit()
    c = mapping.candidates(db, "Infosys and No Price Industries sign a deal")
    assert "INFY" in c and "NOPRICE" not in c  # only stocks Syena can price are proposed
    class R:
        def __init__(self, content, ctype):
            self.content, self.headers, self.text = content, {"content-type": ctype}, content.decode("latin-1")
        def raise_for_status(self):
            pass
    page = b'<html><a href="https://nsearchives.nseindia.com/corporate/X.pdf">Attachment</a></html>'
    calls = []
    def get(u):
        calls.append(u)
        return R(page, "text/html") if u.endswith(".html") else R(b"%PDF-1.4 not really", "application/pdf")
    monkeypatch_pdf = S.pdf_text
    S.pdf_text = lambda data, max_chars=6000: "Revenue Rs 500 crore"
    try:
        assert S.filing_text("https://www.nseindia.com/a.html", get=get) == "Revenue Rs 500 crore"
        assert calls == ["https://www.nseindia.com/a.html", "https://nsearchives.nseindia.com/corporate/X.pdf"]
    finally:
        S.pdf_text = monkeypatch_pdf


def test_small_company_filtered_before_any_ai_call(db, user, fake_llm, md):
    from datetime import date as _date

    from syena.models import Stock, StockDaily

    now = _news_now(md)
    db.add(Stock(symbol="INFY", active=True))
    db.add(StockDaily(symbol="INFY", day=_date(2026, 10, 15), close=1500, market_cap_cr=300))
    db.commit()
    why = pipeline.pre_ai_liquidity(db, "INFY", md, now)
    assert why and "market cap ₹300 cr" in why
    item = NewsItem(source="t", url="u", title="Infosys wins order", published_at=now, content_hash="h-pre")
    db.add(item)
    db.commit()
    before = db.scalar(select(func.count()).select_from(AICall))
    pipeline.record_filtered(db, "INFY", [item], why)
    d = db.scalar(select(Decision).order_by(Decision.id.desc()))
    assert d.type == DecisionType.REJECTED and "no AI cost" in d.summary
    assert db.scalar(select(func.count()).select_from(AICall)) == before


def test_technicals_brief_is_short():
    tech = {"available": True, "close": 100, "sma50": 95, "sma200": 90, "rsi14": 55, "macd_hist": 0.4,
            "reasons": ["close above SMA50", "candles: Hammer (bullish, 01 Oct)"] + [f"x{i} " * 40 for i in range(20)]}
    out = pipeline.technicals_brief(tech)
    assert len(out) <= 1200 and "Hammer" in out and out.startswith("close 100")


def test_cached_prompt_tokens_cost_a_tenth(db):
    from syena.ai import llm

    full = llm.cost(db, "claude-sonnet-5-5", 10_000, 500)
    cached = llm.cost(db, "claude-sonnet-5-5", 2_000, 500, cache_read=8_000)
    assert cached < full * 0.5


def test_day_move_check_blocks_chasing(db):
    class MD:
        def candles(self, sym, iv, start, end):
            idx = pd.DatetimeIndex(["2026-10-22 03:45", "2026-10-23 03:45", "2026-10-26 03:45"], tz="UTC")
            return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [1400.0, 1388.0, 1584.0], "volume": 1.0}, index=idx)
    now = datetime(2026, 10, 26, 9, 40, tzinfo=timezone.utc)   # 15:10 IST, after a 14% rally
    ck = pipeline.day_move_check(db, MD(), "ATHERENERG", now, "BUY", 1584.4)
    assert not ck.passed and "+14.15%" in ck.detail and "too late" in ck.detail
    assert pipeline.day_move_check(db, MD(), "ATHERENERG", now, "BUY", 1420.0).passed     # +2.3%: fine
    assert pipeline.day_move_check(db, MD(), "ATHERENERG", now, "SELL", 1584.4).passed    # a short isn't chasing a rally
