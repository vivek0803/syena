"""Broad news mapped to affected stocks, which then go through the normal analysis and gate."""
from datetime import timedelta

from sqlalchemy import select

from syena.models import NewsAnalysis, NewsItem, Signal, Stock
from syena.news import macro, pipeline
from tests.test_news import _news_now, pf


def _item(db, title, source="ET markets", url="https://et/x", when=None):
    from syena.db import utcnow

    it = NewsItem(source=source, url=url, title=title, published_at=when or utcnow(), content_hash=title[:60])
    db.add(it)
    db.commit()
    return it


def test_keyword_filter_skips_filings_and_company_news(db):
    assert macro.looks_macro(_item(db, "EU announces sanctions and tariffs on Indian exports"))
    assert not macro.looks_macro(_item(db, "Infosys wins order", source="NSE announcements", url="https://nseindia.com/a"))
    assert not macro.looks_macro(_item(db, "Stock X rallies 3% in trade"))


def test_map_impact_keeps_real_confident_stocks_only(db, fake_llm):
    db.add_all([Stock(symbol="INFY", name="Infosys", industry="Computers - Software & Consulting", active=True),
                Stock(symbol="TCS", name="Tata Consultancy Services", industry="Computers - Software & Consulting", active=True)])
    db.commit()
    it = _item(db, "EU slaps tariffs on Indian IT and textile exports")
    out = macro.map_impact(db, it)
    syms = [o["symbol"] for o in out]
    assert "INFY" in syms and all(o["direction"] == "negative" for o in out)   # the 30%-confidence theme is dropped
    assert db.get(NewsItem, it.id).status == "MACRO"
    rec = db.scalar(select(NewsAnalysis).where(NewsAnalysis.stage == "macro"))
    assert rec.result["stocks"] and "INFY" in rec.symbols


def test_macro_note_reaches_the_stock_analysis(db, user, fake_llm, md):
    now = _news_now(md)
    db.add(Stock(symbol="INFY", name="Infosys", industry="Computers - Software & Consulting", active=True))
    db.commit()
    it = _item(db, "EU slaps tariffs on Indian IT exports", when=now - timedelta(minutes=5))
    o = macro.map_impact(db, it)[0]
    sig = pipeline.process_symbol(db, "INFY", [it], md, "macro", pf, now=now, macro_note=macro.note_for(o))
    assert db.get(Signal, sig.id) and "Macro link made by Syena" in fake_llm.prompts[-1]
