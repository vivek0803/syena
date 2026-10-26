"""Daily missed-moves review: each big mover classified by what the news pipeline did."""
from datetime import date, datetime, timedelta

from syena.market.calendar import IST
from syena.models import Decision, DecisionType, NewsAnalysis, NewsItem, Signal, Stock, StockDaily, Style, Trade, TradeStatus
from syena.news import review

DAY = date(2026, 10, 21)  # a Wednesday


def _setup(db):
    def stock(sym, name, sector, chg, cap=5000):
        db.add(Stock(symbol=sym, name=name, sector=sector, active=True))
        db.add(StockDaily(symbol=sym, day=DAY, close=100, change_pct=chg, market_cap_cr=cap))
    stock("WINR", "Winner Industries", "Capital Goods", 9.0)      # traded
    stock("REJX", "Rejected Corp", "Capital Goods", 7.0)          # caught, rejected by priced_in
    stock("LATE", "Late News Ltd", "Chemicals", 6.0)              # news arrived stale
    stock("SECT", "Sector Mover", "Metals", 5.0)                  # no news, sector up
    for i in range(5):
        stock(f"MET{i}", f"Metal {i}", "Metals", 3.0)
    stock("DROP", "Dropper Ltd", "Pharma", -8.0)                  # no news anywhere
    stock("CHAS", "Chaser Ltd", "Auto", 10.0)                     # bought after most of the move
    stock("CONF", "Confidence Ltd", "Auto", 6.5)                  # score under the threshold
    stock("TINY", "Tiny Ltd", "Pharma", 15.0, cap=200)            # too small: left out
    t = datetime(2026, 10, 21, 10, 0, tzinfo=IST)

    def news(title, status, sym=None, action=None, dec=None, entry=None):
        it = NewsItem(source="ET", url=f"u{title}", title=title, published_at=t, content_hash=title, status=status)
        db.add(it)
        db.flush()
        if sym and action:
            sig = Signal(source="news", symbol=sym, action=action, confidence=85, ts=t)
            db.add(sig)
            db.flush()
            db.add(NewsAnalysis(news_item_id=it.id, stage="analysis", relevant=True, symbols=[sym], event_type="order_win",
                                result={"action": action, "confidence": 85, "signal_id": sig.id}, summary="material order"))
            if dec:
                tid = None
                if entry:
                    tr = Trade(source="news", mode="PAPER", style=Style.SWING, stock=sym, side="BUY", qty=1, invested_amount=entry,
                               entry_price=entry, status=TradeStatus.OPEN)
                    db.add(tr)
                    db.flush()
                    tid = tr.id
                db.add(Decision(type=dec[0], side="BUY", symbol=sym, signal_id=sig.id, summary=dec[1], mode="PAPER", source="news",
                                checks_json=dec[2], trade_id=tid))
    news("Winner Industries bags big order", "ANALYSED", "WINR", "BUY", (DecisionType.PLACED, "PLACED BUY 10", []))
    news("Rejected Corp wins contract", "ANALYSED", "REJX", "BUY",
         (DecisionType.REJECTED, "REJECTED by priced_in", [{"name": "priced_in", "passed": False, "detail": "+3.1% since the news"}]))
    news("Late News Ltd capacity expansion", "STALE")
    news("Chaser Ltd shares zoom on results", "ANALYSED", "CHAS", "BUY", (DecisionType.PLACED, "PLACED BUY 1", []), entry=98.0)
    news("Confidence Ltd wins order", "ANALYSED", "CONF", "BUY",
         (DecisionType.REJECTED, "REJECTED by confidence", [{"name": "confidence", "passed": False, "detail": "68 vs 70"}]))
    db.commit()


def test_movers_are_classified(db):
    _setup(db)
    rep = review.build(db, DAY, use_ai=False)
    by = {m["symbol"]: m for m in rep["gainers"] + rep["losers"]}
    assert by["WINR"]["class"] == "traded_right"
    assert by["REJX"]["class"] == "rejected" and "priced in" in by["REJX"]["why"]
    assert by["LATE"]["class"] == "dropped" and "too late" in by["LATE"]["why"]
    assert by["SECT"]["class"] == "sector" and "Metals" in by["SECT"]["why"]
    assert by["DROP"]["class"] == "no_news" and by["DROP"] in rep["losers"]
    assert "TINY" not in by                                            # below the market-cap floor
    assert by["CHAS"]["class"] == "traded_late" and "already happened" in by["CHAS"]["why"]
    learn = rep["learning"]
    assert learn["evidence"] == 3 and len(learn["lessons"]) == 2      # "more weight for order wins" (+ WINR's support) + "late buys"
    from syena.models import Lesson
    texts = [l.text for l in db.query(Lesson).all()]
    assert any("order win" in t and "more weight" in t for t in texts) and any("too late" in t for t in texts)
    again = review.build(db, DAY, use_ai=False)                        # rebuilding the same day adds no duplicate evidence
    assert sum(l.support_count for l in db.query(Lesson).all()) == 3
    assert review.get(db)["report"]["day"] == DAY.isoformat()
    assert "big movers" in review.telegram_summary(rep)


def test_review_api(authed):
    assert authed.get("/api/review/missed").json() == {"days": [], "report": None}
    assert authed.post("/api/review/missed/run").status_code == 409   # no prices yet
