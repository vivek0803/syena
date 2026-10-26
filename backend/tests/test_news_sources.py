"""Freshness (2 h in market hours, after-hours news kept for the next open), dedupe, Telegram channels, images."""
import pandas as pd
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from syena.execution.brokers import PaperBroker
from syena.market.calendar import IST
from syena.models import AICall, NewsAnalysis, NewsItem
from syena.news import pipeline, sources

NOW = datetime(2026, 10, 16, 11, 0, tzinfo=IST)  # Friday, market open
pf = lambda db, mode: PaperBroker()  # noqa: E731


def raw(title, mins_ago=2, src="feed", body="", **kw):
    return sources.RawItem(src, f"https://x/{abs(hash((title, src)))}", title, body, NOW - timedelta(minutes=mins_ago), **kw)


TG_HTML = """
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="moneypurseadv/101">
 <a class="tgme_widget_message_photo_wrap 1" style="width:800px;background-image:url('https://cdn4.telesco.pe/file/abc.jpg')"></a>
 <div class="tgme_widget_message_text js-message_text" dir="auto"><b>Infosys</b> wins $2 billion deal<br/>Big order from US bank &amp; more</div>
 <a class="tgme_widget_message_date" href="https://t.me/moneypurseadv/101"><time datetime="2026-10-16T05:28:00+00:00" class="time">10:58</time></a>
</div></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="moneypurseadv/102">
 <a class="tgme_widget_message_photo_wrap 2" style="width:800px;background-image:url('https://cdn4.telesco.pe/file/chart.jpg')"></a>
 <a class="tgme_widget_message_date" href="https://t.me/moneypurseadv/102"><time datetime="2026-10-16T05:29:00+00:00" class="time">10:59</time></a>
</div></div>
"""


def test_parse_telegram_channel_posts_with_images():
    items = sources.parse_telegram("moneypurseadv", TG_HTML)
    assert [i.key for i in items] == ["tg:moneypurseadv/101", "tg:moneypurseadv/102"]
    a, b = items
    assert a.title == "Infosys wins $2 billion deal" and "Big order from US bank & more" in a.body
    assert a.images == ["https://cdn4.telesco.pe/file/abc.jpg"] and a.source == "Telegram @moneypurseadv"
    assert a.published_at == datetime(2026, 10, 16, 5, 28, tzinfo=timezone.utc)
    assert b.title == "[image post]" and b.body == "" and b.images


def test_old_news_is_stale_and_never_reaches_ai(db, user, fake_llm, md):
    stats = pipeline.run_cycle(db, md, fetch=lambda f: [raw("Infosys wins large multi-year deal", mins_ago=60 * 24 * 5)],
                               broker_factory=pf, now=NOW)
    item = db.scalar(select(NewsItem))
    assert item.status == "STALE" and stats["triaged"] == 0 and not fake_llm.prompts


def test_freshness_window():
    from datetime import datetime as dt

    from syena.market.calendar import IST

    class DB:  # fresh_until only reads settings
        pass

    import syena.settings_store as ss
    get = lambda db, k: ss._merge(ss.DEFAULTS[k], None)  # noqa: E731
    orig, ss.get = ss.get, get
    try:
        wed_11 = dt(2026, 10, 21, 11, 0, tzinfo=IST)          # Wednesday, market open
        assert pipeline.fresh_until(DB(), wed_11) == dt(2026, 10, 21, 13, 0, tzinfo=IST)  # 2 hours
        wed_15 = dt(2026, 10, 21, 15, 0, tzinfo=IST)          # 2 hours would pass the close: next open instead
        assert pipeline.fresh_until(DB(), wed_15) == dt(2026, 10, 22, 9, 30, tzinfo=IST)
        wed_19 = dt(2026, 10, 21, 19, 30, tzinfo=IST)         # evening results: tradeable at the next open
        assert pipeline.fresh_until(DB(), wed_19) == dt(2026, 10, 22, 9, 30, tzinfo=IST)
        thu_8 = dt(2026, 10, 22, 8, 0, tzinfo=IST)            # early morning: today's open
        assert pipeline.fresh_until(DB(), thu_8) == dt(2026, 10, 22, 9, 30, tzinfo=IST)
        fri_20 = dt(2026, 10, 23, 20, 0, tzinfo=IST)          # Friday evening: Monday's open
        assert pipeline.fresh_until(DB(), fri_20) == dt(2026, 10, 26, 9, 30, tzinfo=IST)
    finally:
        ss.get = orig


def test_priced_in_check(db):
    class MD:
        def candles(self, sym, iv, start, end):
            idx = pd.date_range("2026-10-21 04:00", periods=12, freq="5min", tz="UTC")
            c = [100.0] * 6 + [103.0] * 6                     # jumped 3% after the news at 04:30
            return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)
    news_at = datetime(2026, 10, 21, 4, 30, tzinfo=timezone.utc)
    ck = pipeline.priced_in_check(db, MD(), "INFY", news_at, news_at + timedelta(minutes=30), "BUY", 103.0)
    assert not ck.passed and "+3.00%" in ck.detail
    assert pipeline.priced_in_check(db, MD(), "INFY", news_at, news_at + timedelta(minutes=30), "SELL", 103.0).passed


def test_same_story_from_several_sources_goes_to_ai_once(db, user):
    items = pipeline.ingest(db, [
        raw("Infosys wins $1.5 billion deal from European bank - Moneycontrol", src="Moneycontrol"),
        raw("Infosys bags $1.5 billion deal from European bank", src="ET"),
        raw("Infosys Ltd wins $1.5 bn deal from a European bank | Mint", src="Mint"),
        raw("TCS Q2 results: profit rises 9%", src="ET"),
    ], now=NOW)
    status = {i.source + ":" + i.title[:12]: i.status for i in items}
    assert sum(s == "NEW" for s in status.values()) == 2, status
    assert sum(s == "DUPLICATE" for s in status.values()) == 2


def test_story_first_seen_stale_is_not_revived_by_another_source(db, user):
    pipeline.ingest(db, [raw("Infosys wins $1.5 billion deal from European bank", mins_ago=30)], now=NOW)
    later = pipeline.ingest(db, [raw("Infosys bags $1.5 billion deal from European bank", src="ET", mins_ago=1)], now=NOW)
    assert later[0].status == "DUPLICATE"


def test_same_stock_same_event_within_30_min_is_analysed_once(db, user, fake_llm, md):
    s1 = pipeline.run_cycle(db, md, fetch=lambda f: [raw("Infosys wins large multi-year deal")], broker_factory=pf, now=NOW)
    assert s1["signals"] == 1
    s2 = pipeline.run_cycle(db, md, fetch=lambda f: [raw("Mega contract for IT major from global retailer", src="other",
                                                         body="Infosys signs agreement")], broker_factory=pf,
                            now=NOW + timedelta(minutes=5))
    assert s2["triaged"] == 1 and s2["signals"] == 0 and s2["symbol_duplicates"] == 1
    assert len(list(db.scalars(select(NewsAnalysis).where(NewsAnalysis.stage == "analysis")))) == 1


def test_telegram_images_are_read_before_triage(db, user, fake_llm, md):
    tg = sources.parse_telegram("moneypurseadv", TG_HTML)
    for i in tg:
        i.published_at = NOW - timedelta(minutes=2)
    got = []
    stats = pipeline.run_cycle(db, md, fetch=lambda f: [], fetch_telegram=lambda ch: tg, broker_factory=pf, now=NOW,
                               http_get=lambda u: got.append(u) or ("image/jpeg", b"\xff\xd8fake"))
    assert stats["images_read"] == 2 and len(got) == 2
    img_post = db.scalar(select(NewsItem).where(NewsItem.url.like("%/102")))
    assert "[Text read from image]" in img_post.body and img_post.title.startswith("Infosys (INFY) breakout")
    assert db.scalar(select(AICall).where(AICall.prompt_name == "image_reader")) is not None
    assert any("<untrusted" in p for p in fake_llm.prompts)


def test_image_with_no_market_content_is_ignored(db, user, fake_llm):
    fake_llm.image_text = "NO MARKET CONTENT"
    item = NewsItem(source="Telegram @x", url="u", title="[image post]", body="", published_at=NOW, content_hash="tg1",
                    image_urls=["https://cdn/x.jpg"])
    db.add(item)
    db.commit()
    assert pipeline.read_images(db, item, http_get=lambda u: ("image/png", b"x")) == 1
    assert item.body == "" and item.title == "[image post]"
