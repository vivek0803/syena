"""Freshness (15 min), cross-source dedupe, Telegram channel scraping and image reading."""
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
    stats = pipeline.run_cycle(db, md, fetch=lambda f: [raw("Infosys wins large multi-year deal", mins_ago=16)],
                               broker_factory=pf, now=NOW)
    item = db.scalar(select(NewsItem))
    assert item.status == "STALE" and stats["triaged"] == 0 and not fake_llm.prompts


def test_item_that_ages_past_15_minutes_while_waiting_is_skipped(db, user, fake_llm, md):
    pipeline.ingest(db, [raw("Infosys wins large multi-year deal", mins_ago=10)], now=NOW)
    stats = pipeline.run_cycle(db, md, fetch=lambda f: [], broker_factory=pf, now=NOW + timedelta(minutes=6))
    assert stats["stale"] == 1 and not fake_llm.prompts


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
