"""News sources: RSS/Atom feeds (incl. NSE corporate announcements) and public Telegram channels, set in Settings.

Telegram channels are read from their public web preview (https://t.me/s/<channel>), so no Telegram login
or bot admin rights are needed. Photo URLs are kept so the pipeline can read text out of the images.
"""
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
    images: list[str] | None = None  # photo URLs (Telegram posts)
    key: str | None = None  # stable id when the title is not unique (e.g. "tg:channel/123")


def _clean(html: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    return " ".join(t.split())[:4000]


_IST = timezone(timedelta(hours=5, minutes=30))
_DATE_FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d %b %Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S")
# Per-source health from the latest fetch: {"name": {"ok": bool, "items": n, "error": str|None, "at": iso}}
health: dict[str, dict] = {}


def parse_date(entry) -> datetime | None:
    """feedparser handles RFC dates; NSE/BSE feeds use '19-Oct-2026 20:45:12' (Indian time), parsed here."""
    ts = entry.get("published_parsed") or entry.get("updated_parsed")
    if ts:
        return datetime(*ts[:6], tzinfo=timezone.utc)
    raw = (entry.get("published") or entry.get("updated") or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=_IST).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _note(name: str, ok: bool, items: int = 0, error: str | None = None) -> None:
    health[name] = {"ok": ok, "items": items, "error": (error or "")[:200] or None, "at": datetime.now(timezone.utc).isoformat()}


def fetch_feed(name: str, url: str, timeout: float = 15.0) -> list[RawItem]:
    import feedparser

    r = httpx.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
                  timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        published = parse_date(e) or datetime.now(timezone.utc)
        title = _clean(e.get("title", ""))
        if not title:
            continue
        out.append(RawItem(name, e.get("link", url), title, _clean(e.get("summary", "") or e.get("description", "")), published))
    return out


def fetch_all(feeds: list[dict]) -> list[RawItem]:
    items = []
    for f in feeds:
        if f.get("enabled") is False:
            continue
        try:
            got = fetch_feed(f["name"], f["url"])
            items.extend(got)
            _note(f["name"], True, len(got))
        except Exception as e:
            _note(f["name"], False, 0, str(e))
            log.warning("feed fetch failed", extra={"feed": f["name"], "error": str(e)})
    return items


# ---------------------------------------------------------------- Telegram public channels
_TG_POST = re.compile(r'data-post="([^"/]+/\d+)"')
_TG_TEXT = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_TG_PHOTO = re.compile(r'tgme_widget_message_photo_wrap[^>]*background-image:url\([\'"]?([^\'")]+)')
_TG_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')


def _tg_text(html: str) -> str:
    html = re.sub(r"<br\s*/?>", "\n", html)
    t = re.sub(r"<[^>]+>", " ", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    return "\n".join(" ".join(line.split()) for line in t.splitlines() if line.strip())[:4000]


def parse_telegram(channel: str, html: str) -> list[RawItem]:
    out = []
    starts = [m.start() for m in _TG_POST.finditer(html)] + [len(html)]
    for i, m in enumerate(_TG_POST.finditer(html)):
        chunk = html[m.start(): starts[i + 1]]
        post = m.group(1)
        tm = _TG_TIME.search(chunk)
        if not tm:
            continue
        published = datetime.fromisoformat(tm.group(1).replace("Z", "+00:00")).astimezone(timezone.utc)
        txt = _TG_TEXT.search(chunk)
        text = _tg_text(txt.group(1)) if txt else ""
        photos = list(dict.fromkeys(_TG_PHOTO.findall(chunk)))[:4]
        if not text and not photos:
            continue
        first = text.split("\n", 1)[0] if text else "[image post]"
        out.append(RawItem(f"Telegram @{channel}", f"https://t.me/{post}", first[:300], text, published,
                           images=photos or None, key=f"tg:{post}"))
    return out


def fetch_telegram(channel: str, timeout: float = 15.0) -> list[RawItem]:
    channel = channel.strip().removeprefix("https://").removeprefix("t.me/").removeprefix("s/").strip("/@ ")
    r = httpx.get(f"https://t.me/s/{channel}", headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return parse_telegram(channel, r.text)


def fetch_telegram_all(channels: list[str]) -> list[RawItem]:
    items = []
    for c in channels or []:
        name = "Telegram @" + c.strip().removeprefix("https://").removeprefix("t.me/").removeprefix("s/").strip("/@ ")
        try:
            got = fetch_telegram(c)
            items.extend(got)
            _note(name, True, len(got))
        except Exception as e:
            _note(name, False, 0, str(e))
            log.warning("telegram channel fetch failed", extra={"channel": c, "error": str(e)})
    return items


_PDF_LINK = re.compile(r'https?://[^"\'\s<>]+?\.pdf', re.I)
FILING_MARK = "[From the filing]"


def pdf_text(data: bytes, max_chars: int = 6000) -> str:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages[:6]:  # results / order details are on the first pages
        parts.append(page.extract_text() or "")
        if sum(len(p) for p in parts) > max_chars:
            break
    return " ".join(" ".join(parts).split())[:max_chars]


def filing_text(url: str, timeout: float = 15.0, get=None) -> str | None:
    """Text of an exchange filing: the PDF itself, or the first PDF linked from the announcement page.
    The RSS item is only a headline; the numbers (order value, results) are in the attachment."""
    get = get or (lambda u: httpx.get(u, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=timeout, follow_redirects=True))
    r = get(url)
    r.raise_for_status()
    data, ctype = r.content, r.headers.get("content-type", "")
    if "pdf" not in ctype and not data[:5].startswith(b"%PDF"):
        m = _PDF_LINK.search(r.text if hasattr(r, "text") else data.decode("utf-8", "ignore"))
        if not m:
            return None
        r = get(m.group(0))
        r.raise_for_status()
        data = r.content
    if len(data) > 8_000_000 or not data[:5].startswith(b"%PDF"):
        return None
    text = pdf_text(data)
    return text or None


def download_image(url: str, max_bytes: int = 4_500_000, timeout: float = 15.0) -> tuple[str, bytes] | None:
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    if not ctype.startswith("image/") or len(r.content) > max_bytes:
        return None
    return ctype if ctype in ("image/jpeg", "image/png", "image/gif", "image/webp") else "image/jpeg", r.content


# ---------------------------------------------------------------- dedupe
_STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "ltd", "limited", "is", "are", "as", "at", "by", "with",
         "its", "it", "from", "this", "that", "be", "has", "have", "after", "says", "said", "report", "reports", "news",
         "india", "indian", "shares", "share", "stock", "stocks", "today", "update", "live", "rs", "cr", "crore"}
# " - Moneycontrol", " | Mint", " : ET" etc. added by aggregators
_SUFFIX = re.compile(r"\s+[-|:–—]\s+(moneycontrol|mint|livemint|economic times|et markets|et|business standard|"
                     r"reuters|cnbc[- ]?tv18|ndtv profit|zee business|financial express)\s*$", re.I)


def normalise_title(t: str) -> str:
    t = _SUFFIX.sub("", t or "")
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return " ".join(w for w in t.split() if w not in _STOP)


def content_hash(title: str, key: str | None = None) -> str:
    return hashlib.sha256((key or normalise_title(title)).encode()).hexdigest()


def jaccard(a: str, b: str) -> float:
    sa, sb = set(normalise_title(a).split()), set(normalise_title(b).split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


# Words that make up templates ("X buy above 100 target 110 SL 95", "X Ltd - Trading Window"): two items that share
# only these are different stories about different companies, not duplicates.
_TEMPLATE = {"buy", "sell", "short", "long", "above", "below", "near", "around", "target", "targets", "tgt", "sl", "stop", "loss",
             "stoploss", "cmp", "entry", "exit", "call", "calls", "intraday", "positional", "swing", "btst", "stbt", "add",
             "hold", "book", "profit", "profits", "trade", "trading", "window", "closure", "board", "meeting", "outcome", "intimation",
             "results", "result", "financial", "quarter", "quarterly", "q1", "q2", "q3", "q4", "fy", "fy26", "fy27", "announcement",
             "disclosure", "regulation", "reg", "compliance", "certificate", "newspaper", "publication", "investor", "analyst",
             "presentation", "allotment", "esop", "dividend", "record", "date", "updates", "general", "copy", "notice", "agm", "egm",
             "postal", "ballot", "scrutinizer", "credit", "rating", "press", "release", "change", "director", "appointment",
             "resignation", "rs", "crore", "lakh", "per", "cent", "percent", "up", "down", "gains", "falls", "rises", "jumps", "surges",
             "slips", "nse", "bse", "sebi", "intraday", "level", "levels", "support", "resistance", "breakout", "view"}


def _names(tokens: set[str]) -> set[str]:
    return {t for t in tokens if t not in _TEMPLATE and not t.isdigit() and len(t) > 1}


def same_story(a: str, b: str) -> bool:
    """Same news re-published by another source: high word overlap (or one headline contained in the other) AND
    the same distinctive words (company, event). Template look-alikes about different stocks are not duplicates."""
    sa, sb = set(normalise_title(a).split()), set(normalise_title(b).split())
    if not sa or not sb:
        return False
    na, nb = _names(sa), _names(sb)
    if not na or not nb or len(na & nb) / len(na | nb) < 0.5:
        return False
    inter = len(sa & sb)
    if inter / len(sa | sb) >= 0.6:
        return True
    return min(len(sa), len(sb)) >= 4 and inter / min(len(sa), len(sb)) >= 0.8


# Routine exchange filings: dropped before any AI call (cheap keyword check on the subject line).
_ROUTINE = re.compile(
    r"trading window|closure of trading|74\s*\(5\)|reg(ulation)?\.?\s*74|reg(ulation)?\.?\s*(39|40|57|51)\b|"
    r"loss of (share )?certificate|duplicate (share )?certificate|issue of duplicate|newspaper (publication|advertisement)|"
    r"copy of newspaper|analysts?\s*/?\s*institutional investor meet|investor meet|schedule of (analyst|investor)|"
    r"esop|esos|allotment of (equity )?shares under|change in (registered office|address|rta|registrar)|"
    r"compliance officer|certificate under|xbrl|shareholding pattern|statement of investor complaints|"
    r"reconciliation of share capital|annual secretarial compliance|record date for|book closure|"
    r"postal ballot|scrutini[sz]er|voting results|updates? on (credit rating|esg)|credit rating.*(reaffirm|affirm)|"
    r"intimation of (board meeting|record date)|clarification sought|spurious", re.I)


def is_routine(title: str, body: str = "") -> bool:
    return bool(_ROUTINE.search(f"{title} {(body or '')[:300]}"))
