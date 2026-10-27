"""News that used to be missed: NSE filings from the same company, brand names, and headline-only articles."""
from datetime import timedelta

from syena.db import utcnow
from syena.models import NewsItem, Stock
from syena.news import mapping, pipeline, sources

NSE_FEED = b"""<rss version="2.0"><channel><title>NSE</title>
<item><title>PB Fintech Limited</title><link>https://nsearchives.nseindia.com/corporate/PB_1.pdf</link>
<description>PB Fintech Limited has informed the Exchange about Board Meeting |SUBJECT: Board Meeting Intimation</description>
<pubDate>27-Oct-2026 21:48:39</pubDate></item>
<item><title>PB Fintech Limited</title><link>https://nsearchives.nseindia.com/corporate/PB_2.pdf</link>
<description>PB Fintech Limited has informed the Exchange regarding an order |SUBJECT: Bagging/Receiving of orders/contracts</description>
<pubDate>27-Oct-2026 21:50:00</pubDate></item>
</channel></rss>"""


def test_nse_feed_titles_carry_the_subject_and_each_filing_its_own_key():
    items = sources.parse_feed("NSE announcements", "https://nse/rss", NSE_FEED)
    assert [i.title for i in items] == ["PB Fintech Limited – Board Meeting Intimation",
                                        "PB Fintech Limited – Bagging/Receiving of orders/contracts"]
    assert items[0].key != items[1].key and "SUBJECT" not in items[0].body


def test_two_filings_from_one_company_are_both_kept(db, user):
    raw = sources.parse_feed("NSE announcements", "https://nse/rss", NSE_FEED)
    for r in raw:
        r.published_at = utcnow() - timedelta(minutes=3)
    got = pipeline.ingest(db, raw)
    assert len(got) == 2 and all(i.status != "DUPLICATE" for i in got)
    # the next day the company files again under the same company-name title: still new
    again = sources.RawItem("NSE announcements", "https://nsearchives.nseindia.com/corporate/PB_3.pdf",
                            "PB Fintech Limited – Board Meeting Intimation", "x", utcnow() - timedelta(minutes=1),
                            key="url:https://nsearchives.nseindia.com/corporate/PB_3.pdf")
    assert len(pipeline.ingest(db, [again])) == 1
    assert db.query(NewsItem).count() == 3


def test_brand_names_map_to_listed_symbols(db, user):
    db.add_all([Stock(symbol="ETERNAL", name="Eternal Ltd", active=True), Stock(symbol="HAL", name="Hindustan Aeronautics", active=True)])
    db.commit()
    mapping._cache.update(v=None)
    c = mapping.candidates(db, "Zomato shares jump as Blinkit orders double; HAL wins Rs 5,000 crore contract")
    assert set(c) == {"ETERNAL", "HAL"}


def test_ai_lookup_rescues_news_with_no_name_match(db, user, fake_llm):
    db.add(Stock(symbol="ETERNAL", name="Eternal Ltd", active=True))
    db.commit()
    mapping._cache.update(v=None)
    fake_llm.companies = {"companies": [{"name": "Eternal Ltd", "symbol": "ETERNAL"}, {"name": "Made Up Ltd", "symbol": "NOPE"}]}
    fake_llm.triage = {"relevant": True, "symbols": ["ETERNAL", "NOPE"], "event_type": "results", "reason": "food delivery results"}
    item = pipeline.ingest(db, [sources.RawItem("ET", "https://x/1", "Food delivery giant posts record quarter", "",
                                                utcnow() - timedelta(minutes=2))])[0]
    assert pipeline.triage(db, item, [])["symbols"] == ["ETERNAL"]


def test_resolve_never_guesses_loosely(db, user):
    db.add_all([Stock(symbol="TATAPOWER", name="Tata Power Company", active=True), Stock(symbol="TATASTEEL", name="Tata Steel", active=True)])
    db.commit()
    mapping._cache.update(v=None)
    assert mapping.resolve(db, [{"name": "Tata", "symbol": None}]) == {}
    assert mapping.resolve(db, [{"name": "Tata Power", "symbol": None}]) == {"TATAPOWER": "Tata Power"}


def test_article_text_reads_paragraphs_not_menus():
    html = ("<html><nav><p>Home Markets News Subscribe to our newsletter for daily updates and more</p></nav>"
            "<p>Short</p><p>Infosys on Monday said it has won a five-year deal worth $1.5 billion from a large European bank.</p>"
            "<script>var x='<p>not this one, a script paragraph that is long enough to count</p>'</script></html>")

    class R:
        text, headers = html, {"content-type": "text/html"}

        def raise_for_status(self):
            pass

    txt = sources.article_text("https://x", get=lambda u: R())
    assert txt.startswith("Infosys on Monday") and "Subscribe" not in txt and "script" not in txt
