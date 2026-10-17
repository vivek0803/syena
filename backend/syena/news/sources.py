"""News sources: RSS/Atom feeds (incl. NSE corporate announcements) configured in Settings."""
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger("syena.news")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 Syena/1.0"


@dataclass
class RawItem:
    source: str
    url: str
    title: str
    body: str
    published_at: datetime


def _clean(html: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    return " ".join(t.split())[:4000]


def fetch_feed(name: str, url: str, timeout: float = 15.0) -> list[RawItem]:
    import feedparser

    r = httpx.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
                  timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime(*ts[:6], tzinfo=timezone.utc) if ts else datetime.now(timezone.utc)
        title = _clean(e.get("title", ""))
        if not title:
            continue
        out.append(RawItem(name, e.get("link", url), title, _clean(e.get("summary", "") or e.get("description", "")), published))
    return out


def fetch_all(feeds: list[dict]) -> list[RawItem]:
    items = []
    for f in feeds:
        try:
            items.extend(fetch_feed(f["name"], f["url"]))
        except Exception as e:
            log.warning("feed fetch failed", extra={"feed": f["name"], "error": str(e)})
    return items


def normalise_title(t: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return " ".join(w for w in t.split() if w not in {"the", "a", "an", "of", "in", "on", "for", "to", "and", "ltd", "limited"})


def content_hash(title: str) -> str:
    return hashlib.sha256(normalise_title(title).encode()).hexdigest()


def jaccard(a: str, b: str) -> float:
    sa, sb = set(normalise_title(a).split()), set(normalise_title(b).split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0
