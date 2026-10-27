"""Shareholding pattern (promoters, FIIs, DIIs, mutual funds, retail) from the filings companies make with NSE.

Every listed company files its shareholding pattern each quarter as an XBRL file on nsearchives.nseindia.com (the
same public archive Syena reads announcements from). The file tags each category with a context, e.g.
InstitutionsForeignMember = foreign institutions (FII/FPI), InstitutionsDomesticMember = domestic institutions
(DII: mutual funds, insurers, banks, pension funds...), with its share of the company as a fraction.

Two ways in:
  - new filings: NSE's shareholding RSS lists the day's filings; read every 30 minutes (cheap, no login);
  - backfill: for stocks with no recent pattern, NSE's quote API lists each company's filings (needs the cookies a
    browser gets from the home page; done a few dozen stocks a night, stops politely if NSE refuses).
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.models import ShareholdingPattern, Stock

log = logging.getLogger("syena.shareholding")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml"
API_URL = "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={symbol}"
CATEGORIES = {  # XBRL member -> field
    "ShareholdingOfPromoterAndPromoterGroupMember": "promoter",
    "InstitutionsForeignMember": "fii",
    "InstitutionsDomesticMember": "dii",
    "MutualFundsOrUTIMember": "mf",
    "InsuranceCompaniesMember": "insurance",
    "PublicShareholdingMember": "public",
    "ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakhMember": "_small",
    "ResidentIndividualShareholdersHoldingNominalShareCapitalInExcessOfRsTwoLakhMember": "_large",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_xbrl(content: bytes) -> dict:
    """{symbol, as_of, promoter, fii, dii, mf, insurance, public, retail, shareholders} in % of shares."""
    root = ET.fromstring(content)
    contexts: dict[str, list[str]] = {}
    for c in root.iter():
        if _local(c.tag) == "context":
            members = [m.text.split(":")[-1] for m in c.iter() if _local(m.tag) == "explicitMember" and m.text]
            contexts[c.get("id")] = members
    out: dict = {}
    for e in root.iter():
        name = _local(e.tag)
        if name == "Symbol" and e.text and "symbol" not in out:
            out["symbol"] = e.text.strip().upper()
        elif name == "DateOfReport" and e.text and "as_of" not in out:
            out["as_of"] = date.fromisoformat(e.text.strip()[:10])
        elif name == "ShareholdingAsAPercentageOfTotalNumberOfShares" and e.text:
            members = contexts.get(e.get("contextRef"), [])
            if len(members) == 1 and members[0] in CATEGORIES:
                field = CATEGORIES[members[0]]
                if field not in out:
                    try:
                        out[field] = round(float(e.text) * 100, 2)
                    except ValueError:
                        pass
        elif name == "NumberOfShareholders" and e.text:
            members = contexts.get(e.get("contextRef"), [])
            if members == ["ShareholdingPatternMember"]:
                try:
                    out["shareholders"] = int(float(e.text))
                except ValueError:
                    pass
    if "_small" in out or "_large" in out:
        out["retail"] = round(out.pop("_small", 0.0) + out.pop("_large", 0.0), 2)
    if not out.get("symbol") or not out.get("as_of"):
        raise ValueError("not a shareholding pattern file")
    return out


def _get(url: str, client: httpx.Client | None = None):
    c = client or httpx
    r = c.get(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return r


def store(db: Session, data: dict, url: str | None) -> ShareholdingPattern | None:
    """Save one pattern (once per company and quarter-end) and refresh the stock's latest figures."""
    sym = data["symbol"]
    if db.get(Stock, sym) is None:
        return None
    row = db.scalar(select(ShareholdingPattern).where(ShareholdingPattern.symbol == sym, ShareholdingPattern.as_of == data["as_of"]))
    if row is None:
        row = ShareholdingPattern(symbol=sym, as_of=data["as_of"])
        db.add(row)
    for k in ("promoter", "fii", "dii", "mf", "insurance", "public", "retail", "shareholders"):
        if data.get(k) is not None:
            setattr(row, k, data[k])
    row.source_url, row.fetched_at = (url or "")[:300] or None, utcnow()
    db.flush()
    apply_latest(db, sym)
    db.commit()
    return row


def apply_latest(db: Session, symbol: str) -> None:
    rows = list(db.scalars(select(ShareholdingPattern).where(ShareholdingPattern.symbol == symbol)
                           .order_by(ShareholdingPattern.as_of.desc()).limit(2)))
    s = db.get(Stock, symbol)
    if not rows or s is None:
        return
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 and (cur.as_of - rows[1].as_of).days <= 120 else None
    s.promoter_holding, s.fii_holding, s.dii_holding = cur.promoter, cur.fii, cur.dii
    s.mf_holding, s.public_holding, s.retail_holding = cur.mf, cur.public, cur.retail
    s.shareholding_date = cur.as_of
    if cur.promoter is not None:
        s.insider_holding = cur.promoter  # the filing beats Yahoo's 'insiders' estimate
    ch = lambda a, b: None if a is None or b is None else round(a - b, 2)  # noqa: E731
    s.promoter_change = ch(cur.promoter, prev.promoter) if prev else None
    s.fii_change = ch(cur.fii, prev.fii) if prev else None
    s.dii_change = ch(cur.dii, prev.dii) if prev else None


def poll_rss(db: Session, get=None) -> dict:
    """Read today's newly filed patterns from NSE's shareholding feed."""
    get = get or _get
    r = get(RSS_URL)
    links = re.findall(rb"<link>(https?://[^<]+SHP_[^<]+\.xml)</link>", r.content)
    have = set(db.scalars(select(ShareholdingPattern.source_url).where(ShareholdingPattern.fetched_at >= utcnow() - timedelta(days=10))))
    done = failed = 0
    for raw in links:
        url = raw.decode()
        if url in have:
            continue
        try:
            if store(db, parse_xbrl(get(url).content), url):
                done += 1
        except Exception as e:
            db.rollback()
            failed += 1
            log.info("shareholding file not read", extra={"url": url, "error": str(e)[:150]})
    return {"stored": done, "failed": failed, "listed": len(links)}


def backfill(db: Session, limit: int = 40, quarters: int = 2, client: httpx.Client | None = None) -> dict:
    """Stocks without a pattern from the last ~4 months: list their filings via NSE's API and read the latest ones."""
    cutoff = date.today() - timedelta(days=130)
    fresh = set(db.scalars(select(ShareholdingPattern.symbol).where(ShareholdingPattern.as_of >= cutoff)))
    todo = [s for (s,) in db.execute(select(Stock.symbol).where(Stock.active.is_(True)).order_by(Stock.symbol)) if s not in fresh][:limit]
    if not todo:
        return {"stored": 0, "checked": 0}
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}, timeout=20, follow_redirects=True)
    stored = refused = 0
    try:
        if own:
            client.get("https://www.nseindia.com/")  # sets the cookies NSE's API expects
        for sym in todo:
            try:
                r = client.get(API_URL.format(symbol=sym), headers={"Accept": "application/json", "Referer": "https://www.nseindia.com/"})
                if r.status_code in (401, 403):
                    refused += 1
                    if refused >= 3:
                        log.warning("NSE refused the shareholding API: backfill paused for tonight")
                        break
                    continue
                r.raise_for_status()
                filings = [f for f in (r.json() or []) if f.get("xbrl")][:quarters]
                for f in filings:
                    try:
                        if store(db, parse_xbrl(client.get(f["xbrl"]).content), f["xbrl"]):
                            stored += 1
                    except Exception as e:
                        db.rollback()
                        log.info("shareholding file not read", extra={"symbol": sym, "error": str(e)[:150]})
            except Exception as e:
                log.info("shareholding list not read", extra={"symbol": sym, "error": str(e)[:150]})
    finally:
        if own:
            client.close()
    return {"stored": stored, "checked": len(todo)}


def history(db: Session, symbol: str, n: int = 8) -> list[dict]:
    rows = db.scalars(select(ShareholdingPattern).where(ShareholdingPattern.symbol == symbol)
                      .order_by(ShareholdingPattern.as_of.desc()).limit(n))
    return [{"as_of": r.as_of.isoformat(), "promoter": r.promoter, "fii": r.fii, "dii": r.dii, "mf": r.mf,
             "retail": r.retail, "public": r.public, "shareholders": r.shareholders} for r in rows]
