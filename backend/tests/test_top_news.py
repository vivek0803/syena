"""Top 10 news of the day: candidates, AI ranking (never trusting invented ids), storage and API."""
from datetime import timedelta

from syena.db import utcnow
from syena.models import NewsAnalysis, NewsItem
from syena.news import topnews


def item(db, title, status="ANALYSED", mins=60, source="ET", symbols=None, action=None):
    it = NewsItem(source=source, url=f"https://x/{abs(hash(title))}", title=title, body="", published_at=utcnow() - timedelta(minutes=mins),
                  content_hash=str(abs(hash(title))), fetched_at=utcnow(), status=status)
    db.add(it)
    db.flush()
    if symbols:
        db.add(NewsAnalysis(news_item_id=it.id, stage="triage", relevant=True, symbols=symbols, event_type="order_win"))
    if action:
        db.add(NewsAnalysis(news_item_id=it.id, stage="analysis", relevant=True, symbols=symbols, summary="big order",
                            result={"action": action, "confidence": 80}))
    db.commit()
    return it


def test_builds_ranked_list_and_drops_invented_or_routine_items(db, user, fake_llm):
    a = item(db, "HAL bags Rs 20,000 crore order for fighter jets", symbols=["HAL"], action="BUY")
    b = item(db, "RBI cuts repo rate by 25 bps", status="MACRO")
    r = item(db, "XYZ Ltd – Trading Window closure", status="IRRELEVANT")
    old = item(db, "Very old story", mins=60 * 40)
    fake_llm.top_news = {"top": [{"id": a.id, "headline": "HAL wins ₹20,000 cr jet order", "why": "Big vs revenue.", "symbols": ["HAL"],
                                  "direction": "positive", "impact": "high"},
                                 {"id": 99999, "headline": "made up", "symbols": []},
                                 {"id": b.id, "headline": "RBI cuts rates", "why": "Cheaper loans.", "symbols": ["Banks"], "direction": "positive", "impact": "high"}],
                         "market_mood": "Upbeat on rate cut."}
    rep = topnews.build(db, notify=False)
    assert [t["news_id"] for t in rep["top"]] == [a.id, b.id] and rep["top"][0]["rank"] == 1
    assert rep["top"][0]["linked"] == ["HAL"] and rep["top"][0]["syena_verdict"] == "BUY 80"
    prompt = fake_llm.prompts[-1]
    assert "HAL bags" in prompt and "Trading Window" not in prompt and "Very old story" not in prompt and r.id and old.id
    assert topnews.get(db)["report"]["market_mood"] == "Upbeat on rate cut."


def test_api(authed, fake_llm):
    assert authed.get("/api/top-news").json() == {"days": [], "report": None}
    r = authed.post("/api/top-news/run").json()
    assert r["top"] == [] and authed.get("/api/top-news").json()["days"] == [r["day"]]
