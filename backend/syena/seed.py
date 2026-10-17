"""Demo data for local development (`syena seed-demo`). Refuses to run outside SYENA_ENV=dev.

Everything is PAPER and clearly synthetic. It exercises every page: decisions with check trails, news with
analyses, trades, orders, lessons at each status, reviews, a backtested algo, portfolio holdings with fund
look-through, eval runs and logs.
"""
import random
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from syena.algos import parser
from syena.algos.backtest import BacktestConfig, run
from syena.db import utcnow
from syena.evals import parser_eval
from syena.execution.brokers import get_wallet
from syena.learning.lessons import create_proposed
from syena.models import (Alert, Algo, Decision, DecisionType, Holding, HoldingSnapshot, IsinMaster, Lesson, MFSchemeHolding,
                          NewsAnalysis, NewsItem, Order, OrderStatus, Signal, Style, SystemLog, Trade, TradeReview, TradeStatus)

R = random.Random(7)
STOCKS = {"INFY": ("INE009A01021", 1520.0, "IT - Software"), "TCS": ("INE467B01029", 3110.0, "IT - Software"),
          "RELIANCE": ("INE002A01018", 1385.0, "Petroleum Products"), "HDFCBANK": ("INE040A01034", 1690.0, "Banks"),
          "ITC": ("INE154A01025", 412.0, "Diversified FMCG"), "SBIN": ("INE062A01020", 815.0, "Banks"),
          "LT": ("INE018A01030", 3550.0, "Construction"), "BHARTIARTL": ("INE397D01024", 1590.0, "Telecom")}


def _checks(fail: str | None = None, conf: float = 78):
    names = [("mode", "system is in PAPER mode: simulated order"), ("not_halted", "kill switch off"),
             ("market_hours", "market open"), ("sizing", "6 × ₹1,520.00 = ₹9,120.00 (band ₹7,000-₹10,000)"),
             ("max_open_positions", "2 open / limit 5"), ("max_trades_per_day", "3 trades today / limit 10"),
             ("max_daily_loss", "today's P&L ₹-210.00 / max loss ₹3,000"), ("per_stock_exposure", "existing ₹0 + new ₹9,120 / cap ₹10,000"),
             ("signal_action", "action BUY"), ("confidence", f"confidence {conf:g} vs threshold 70"),
             ("technicals", "close above SMA50; close above SMA200; MACD histogram positive"), ("market_context", "context score 0.31 (min -0.3)")]
    out = [{"name": n, "passed": n != fail, "detail": d, "data": {}} for n, d in names]
    if fail == "confidence":
        out[9]["detail"] = f"confidence {conf:g} vs threshold 70"
    return out


def seed(db: Session) -> dict:
    now = utcnow()
    w = get_wallet(db)
    algo_rule = parser.parse_deterministic("buy when ema 20 crosses above ema 50, sell when ema 20 crosses below ema 50, stop loss 2%, target 5%").rule
    rng = np.random.default_rng(5)
    idx = pd.date_range(end=now, periods=500, freq="B", tz="UTC")
    c = 1500 * np.exp(np.cumsum(rng.normal(0.0005, 0.013, 500)))
    df = pd.DataFrame({"open": c * (1 + rng.normal(0, .002, 500)), "high": c * 1.01, "low": c * .99, "close": c,
                       "volume": rng.integers(1e5, 1e6, 500)}, index=idx)
    bt = run(df, algo_rule, BacktestConfig(), "INFY")
    bt["config"] = {"symbols": ["INFY"], "timeframe": "day", "style": "SWING", "start": idx[0].isoformat(), "end": idx[-1].isoformat()}
    bt["per_symbol"] = {"INFY": bt["metrics"]}
    algo = Algo(name="EMA 20/50 trend", english_text="buy when ema 20 crosses above ema 50, sell when ema 20 crosses below ema 50, stop loss 2%, target 5%",
                parsed_rule=algo_rule, parser_source="deterministic", style=Style.SWING, run_mode="PAPER", status="RUNNING",
                symbols=["INFY", "TCS", "HDFCBANK"], timeframe="day", backtest_result=bt, backtest_passed=False, started_at=now - timedelta(days=20))
    news_algo = Algo(name="News strategy", kind="news", english_text="AI news-driven signals", style=Style.SWING, run_mode="PAPER",
                     status="RUNNING", symbols=[], timeframe="day")
    db.add_all([algo, news_algo])
    db.flush()

    headlines = [("INFY", "Infosys wins multi-year cloud deal from a European bank", "order_win", 0.6, "BUY", 82),
                 ("TCS", "TCS quarterly profit beats estimates as deal pipeline grows", "earnings", 0.5, "BUY", 74),
                 ("SBIN", "SBI raises lending rates by 10 bps across tenors", "corporate_action", -0.1, "HOLD", 40),
                 ("ITC", "ITC hotels demerger record date announced", "corporate_action", 0.2, "BUY", 61),
                 ("HDFCBANK", "HDFC Bank deposit growth slows in the September quarter", "earnings", -0.4, "SELL", 66)]
    news_ids = {}
    for i, (sym, title, etype, sent, action, conf) in enumerate(headlines):
        ts = now - timedelta(hours=3 * i + 1)
        n = NewsItem(source="Demo feed", url=f"https://example.com/news/{i}", title=title, published_at=ts, content_hash=f"demo{i}", status="ANALYSED")
        db.add(n)
        db.flush()
        news_ids[sym] = n.id
        db.add(NewsAnalysis(news_item_id=n.id, stage="triage", relevant=True, symbols=[sym], event_type=etype, summary="Company-specific and material."))
        sig = Signal(source="news", algo_id=news_algo.id, symbol=sym, action=action, confidence=conf, ts=ts,
                     reasoning=f"{title}. Technicals and market context are broadly supportive.", model="claude-sonnet-5-5",
                     prompt_version="v1", price_at_signal=STOCKS[sym][1], cited=[n.id])
        db.add(sig)
        db.flush()
        db.add(NewsAnalysis(news_item_id=n.id, stage="analysis", relevant=True, symbols=[sym], sentiment=sent, event_type=etype,
                            summary=title + ".", model="claude-sonnet-5-5", prompt_version="v1",
                            result={"action": action, "confidence": conf, "signal_id": sig.id, "inputs": {"news": [{"id": n.id, "title": title}]}}))
        if action == "BUY" and conf < 70:
            db.add(Decision(type=DecisionType.REJECTED, mode="PAPER", source="news", symbol=sym, side="BUY", algo_id=news_algo.id, signal_id=sig.id,
                            confidence=conf, ts=ts, summary=f"REJECTED by confidence: confidence {conf} vs threshold 70",
                            checks_json=_checks("confidence", conf), reason_json={"summary": title, "reasoning": sig.reasoning, "model": "claude-sonnet-5-5", "prompt_version": "v1"},
                            inputs_json={"news": [{"id": n.id, "title": title}]}, lessons_consulted=[], lessons_applied=[]))

    trades = []
    for k in range(14):
        sym = list(STOCKS)[k % len(STOCKS)] if k >= 11 else R.choice(list(STOCKS))
        isin, px, _ = STOCKS[sym]
        qty = int(10000 // px)
        if qty * px < 7000:
            continue
        opened = now - timedelta(days=28 - k * 2, hours=R.randint(0, 4))
        is_open = k >= 11
        pnl = None if is_open else round(R.gauss(60, 260), 2)
        src = "news" if k % 2 else "algo"
        t = Trade(algo_id=news_algo.id if src == "news" else algo.id, source=src, mode="PAPER", style=Style.SWING,
                  algo_condition=algo.english_text if src == "algo" else None, stock=sym, qty=qty, invested_amount=round(qty * px, 2),
                  entry_price=px, stop_loss=round(px * .98, 2), target=round(px * 1.05, 2), charges=round(qty * px * .0022, 2),
                  status=TradeStatus.OPEN if is_open else TradeStatus.CLOSED, created_at=opened, auto_sell=True,
                  news_item_ids=[news_ids[sym]] if src == "news" and sym in news_ids else None,
                  setup={"symbol": sym, "style": "SWING", "source": src, "time_of_day": R.choice(["open", "mid", "close"]),
                         "event_type": "order_win" if src == "news" else None, "confidence": R.choice([72, 78, 84])},
                  last_price=round(px * (1 + R.gauss(0.004, 0.012)), 2))
        if not is_open:
            t.exit_price_executed = round(px + (pnl + t.charges) / qty, 2)
            t.profit_loss, t.closed_at = pnl, opened + timedelta(days=R.randint(1, 5))
            t.exit_reason = "target" if pnl > 0 else R.choice(["stop_loss", "exit_rule"])
        db.add(t)
        db.flush()
        d = Decision(type=DecisionType.PLACED, mode="PAPER", source=src, symbol=sym, side="BUY", algo_id=t.algo_id, trade_id=t.id,
                     confidence=78 if src == "news" else None, ts=opened, summary=f"PLACED BUY {qty} {sym} @ {px} (PAPER)",
                     checks_json=_checks(), reason_json={"rule": "BUY when EMA(20) crosses above EMA(50)"} if src == "algo" else
                     {"summary": "Material order win.", "reasoning": "Deal size is large relative to quarterly revenue [cited].", "model": "claude-sonnet-5-5", "prompt_version": "v1", "risks": ["deal ramp may be slow"]},
                     lessons_consulted=[], lessons_applied=[])
        db.add(d)
        db.flush()
        t.decision_id = d.id
        db.add(Order(idempotency_key=f"demo-{t.id}-ENTRY", trade_id=t.id, mode="PAPER", broker="paper", symbol=sym, side="BUY", qty=qty,
                     order_type="LIMIT", price=round(px * 1.001, 2), product="CNC", purpose="ENTRY", status=OrderStatus.COMPLETE,
                     fill_price=px, filled_qty=qty, charges=round(t.charges / 2, 2), message=f"simulated fill @ {px}", created_at=opened))
        if not is_open:
            db.add(Decision(type=DecisionType.EXITED, mode="PAPER", source=src, symbol=sym, side="SELL", algo_id=t.algo_id, trade_id=t.id,
                            ts=t.closed_at, summary=f"EXITED {sym} @ {t.exit_price_executed}: {t.exit_reason}; P&L ₹{pnl:,.2f}",
                            checks_json=[{"name": "exit_reason", "passed": True, "detail": t.exit_reason, "data": {}},
                                         {"name": "broker", "passed": True, "detail": "simulated fill", "data": {}}]))
            cls = "good-entry/good-exit" if pnl > 0 else R.choice(["bad-luck", "stop-too-tight", "bad-entry"])
            db.add(TradeReview(trade_id=t.id, decision_id=d.id, kind="closed_trade", classification=cls,
                               decision_quality="unsound" if cls == "bad-entry" else "sound", outcome="win" if pnl > 0 else "loss",
                               right=[{"point": "Entry aligned with the trend", "evidence": "EMA20 above EMA50 at entry"}],
                               wrong=[] if pnl > 0 else [{"point": "Stop sat inside normal daily range", "evidence": "ATR 1.9% vs 2% stop"}],
                               counterfactual={"actual_pnl": pnl, "wider_stop_1.5x": {"pnl": round(pnl + 80, 2), "by": "target"}, "skip": {"pnl": 0}},
                               summary=f"{cls.replace('-', ' ')}: {'clean trade' if pnl > 0 else 'outcome worse than the decision deserved' if cls != 'bad-entry' else 'entered into resistance'}",
                               created_at=t.closed_at + timedelta(minutes=10), model="rule-based (demo)"))
        trades.append(t)
    w.cash = round(w.starting_balance + sum(t.profit_loss or 0 for t in trades) - sum(t.invested_amount for t in trades if t.status == TradeStatus.OPEN), 2)

    l1 = create_proposed(db, "News order-win entries in the first hour have faded by midday", {"event_types": ["order_win"], "time_of_day": "open"},
                         {"type": "reduce_confidence", "value": 10}, "review")
    l2 = create_proposed(db, "Algo entries in banks during high VIX hit their stops early", {"sectors": ["Banks"], "vix_regime": "high"},
                         {"type": "tighten_stop", "value": 0.8}, "mining")
    l2.status, l2.support_count, l2.contradiction_count = "TESTING", 6, 1
    l2.validation = {"n": 9, "avg_pnl_delta": 42.5, "total_pnl_delta": 382.5, "passed": True, "note": None}
    l3 = create_proposed(db, "Avoid fresh entries in ITC on corporate-action headlines", {"symbols": ["ITC"], "event_types": ["corporate_action"]},
                         {"type": "avoid"}, "review")
    l3.status, l3.support_count, l3.approved_at, l3.expires_at = "ACTIVE", 7, now - timedelta(days=5), now + timedelta(days=25)
    l3.validation = {"n": 6, "avg_pnl_delta": 61.0, "total_pnl_delta": 366.0, "passed": True, "note": None}
    l1.support_count = 2

    snaps = {}
    for src in ("zerodha", "angel", "cas"):
        s = HoldingSnapshot(source=src, taken_at=now - timedelta(hours=2), note="statement 01-Apr-2026 to 30-Sep-2026" if src == "cas" else None)
        db.add(s)
        db.flush()
        snaps[src] = s.id
    for sym, qty, src in (("RELIANCE", 40, "zerodha"), ("INFY", 25, "zerodha"), ("HDFCBANK", 20, "zerodha"), ("ITC", 120, "angel"), ("TCS", 8, "angel")):
        isin, px, sector = STOCKS[sym]
        db.add(Holding(snapshot_id=snaps[src], source=src, asset_class="equity", symbol=sym, isin=isin, name=sym, qty=qty,
                       avg_price=round(px * .86, 2), ltp=px, value=round(qty * px, 2)))
        db.merge(IsinMaster(isin=isin, symbol=sym, name=sym, sector=sector))
    funds = [("INF000DEMO01", "Demo Flexi Cap Fund - Direct Growth", 85000, {"RELIANCE": 8.2, "HDFCBANK": 9.1, "INFY": 6.4, "BHARTIARTL": 4.0, "LT": 3.5}),
             ("INF000DEMO02", "Demo Large Cap Fund - Direct Growth", 120000, {"RELIANCE": 9.0, "HDFCBANK": 10.2, "TCS": 5.1, "ITC": 3.8, "SBIN": 3.2}),
             ("INF000DEMO03", "Demo Nifty 50 Index Fund - Direct Growth", 60000, {"RELIANCE": 8.8, "HDFCBANK": 12.9, "INFY": 5.0, "TCS": 3.4, "ITC": 3.9, "BHARTIARTL": 4.4, "LT": 3.8, "SBIN": 2.9})]
    for isin, name, value, weights in funds:
        db.add(Holding(snapshot_id=snaps["cas"], source="cas", asset_class="mf", isin=isin, name=name, qty=value / 50, ltp=50, value=value, amc="Demo AMC", folio="123/45"))
        for sym, w_ in weights.items():
            si, _, sector = STOCKS[sym]
            db.add(MFSchemeHolding(scheme_key=isin, scheme_name=name, as_of=date(2026, 9, 30), holding_isin=si, holding_name=sym, pct_nav=w_, sector=sector, asset_type="equity"))
    db.add_all([Alert(kind="sell", symbol="HDFCBANK", message="negative_news: HDFC Bank deposit growth slows", status="REJECTED", created_at=now - timedelta(hours=5)),
                Alert(kind="summary", message="📊 Daily summary: PAPER P&L ₹412", created_at=now - timedelta(hours=20))])
    for lvl, mod, msg in (("INFO", "syena.engine", "entry processed"), ("WARNING", "syena.news", "feed fetch failed"), ("INFO", "syena.worker", "worker started")):
        db.add(SystemLog(level=lvl, module=mod, message=msg, stock="INFY" if mod == "syena.engine" else None, extra={"demo": True}))
    db.commit()
    parser_eval.run_suite(db, {}, "seed")
    return {"trades": len(trades), "lessons": 3, "news": len(headlines)}
