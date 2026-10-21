"""One sector classification for every stock, using NSE's own sector names.

1. NSE's index constituent files carry an "Industry" column (NSE's macro-sector, e.g. "Capital Goods",
   "Fast Moving Consumer Goods"). Nifty Total Market + Microcap 250 cover roughly the 1,000 largest companies.
   Those are authoritative and refreshed weekly.
2. Every other stock gets its Yahoo sector/industry translated into the same NSE names, so a sector list never
   splits into "Technology" vs "Information Technology".
"""
import csv
import io
import logging
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.models import AppSetting, Stock

log = logging.getLogger("syena.sectors")

KEY = "_nse_sectors"  # app_settings: {"at": iso, "map": {"RELIANCE": "Oil Gas & Consumable Fuels", ...}}
REFRESH_DAYS = 7
# Broadest lists first; later ones only fill gaps. Any that fail to download are skipped.
SOURCE_FILES = ("ind_niftytotalmarket_list", "ind_niftymicrocap250_list", "ind_nifty500list",
                "ind_niftysmallcap250list", "ind_niftymidcap150list")

NSE_SECTORS = {
    "Automobile and Auto Components", "Capital Goods", "Chemicals", "Construction", "Construction Materials",
    "Consumer Durables", "Consumer Services", "Diversified", "Fast Moving Consumer Goods", "Financial Services",
    "Forest Materials", "Healthcare", "Information Technology", "Media Entertainment & Publication",
    "Metals & Mining", "Oil Gas & Consumable Fuels", "Power", "Realty", "Services", "Telecommunication", "Textiles",
}
# Yahoo industry keywords -> NSE sector, checked in order (more specific first).
_RULES = [
    (("electrical equipment", "aerospace", "defense", "machinery", "industrial distribution", "tools & accessories",
      "metal fabrication", "pollution", "security & protection", "waste management", "electronic components",
      "scientific & technical instruments", "conglomerate"), None),  # placeholder, handled below
]
_RULES = [
    ("Financial Services", ("bank", "credit services", "capital markets", "insurance", "asset management",
                            "financial", "mortgage", "shell companies")),
    ("Capital Goods", ("electrical equipment", "aerospace", "defense", "machinery", "industrial distribution",
                       "tools & accessories", "metal fabrication", "pollution", "security & protection",
                       "waste management", "electronic components", "scientific & technical instruments",
                       "engineering & construction machinery", "farm & heavy")),
    ("Information Technology", ("software", "information technology", "computer hardware", "it services",
                                "semiconductor", "electronic gaming")),
    ("Telecommunication", ("telecom", "communication equipment")),
    ("Healthcare", ("drug", "pharma", "biotech", "medical", "diagnostics", "health")),
    ("Automobile and Auto Components", ("auto", "recreational vehicle")),
    ("Oil Gas & Consumable Fuels", ("oil", "gas", "coal", "petroleum", "refining", "thermal")),
    ("Power", ("utilities", "renewable", "solar")),
    ("Metals & Mining", ("steel", "aluminum", "aluminium", "copper", "metal", "mining", "gold", "silver")),
    ("Chemicals", ("chemical", "agricultural inputs", "fertili")),
    ("Construction Materials", ("building materials", "cement")),
    ("Construction", ("engineering & construction", "infrastructure operations", "building products")),
    ("Realty", ("real estate",)),
    ("Consumer Services", ("retail", "restaurant", "lodging", "resort", "casino", "travel", "leisure",
                           "education", "personal services", "internet content")),
    ("Textiles", ("textile", "apparel manufacturing")),
    ("Fast Moving Consumer Goods", ("packaged foods", "beverages", "tobacco", "household", "personal products",
                                    "confectioner", "farm products", "food distribution", "grocery")),
    ("Consumer Durables", ("furnishings", "appliances", "consumer electronics", "home improvement",
                           "luxury goods", "footwear", "apparel", "leisure products")),
    ("Media Entertainment & Publication", ("entertainment", "broadcasting", "publishing", "advertising", "media")),
    ("Forest Materials", ("paper", "lumber", "forest", "packaging")),
    ("Diversified", ("conglomerates",)),
    ("Services", ("shipping", "marine", "airline", "railroad", "trucking", "logistics", "airport", "freight",
                  "staffing", "consulting", "rental", "specialty business services")),
]
_SECTOR_FALLBACK = {
    "technology": "Information Technology", "financial services": "Financial Services", "financial": "Financial Services",
    "healthcare": "Healthcare", "energy": "Oil Gas & Consumable Fuels", "utilities": "Power", "real estate": "Realty",
    "basic materials": "Metals & Mining", "communication services": "Telecommunication",
    "consumer defensive": "Fast Moving Consumer Goods", "consumer cyclical": "Consumer Durables",
    "industrials": "Capital Goods",
}
_cache: dict = {"t": 0.0, "map": {}}


def canonical(sector: str | None, industry: str | None) -> str | None:
    """Map any sector/industry label (Yahoo, Screener.in, NSE) onto NSE's sector names."""
    if sector and sector.strip() in NSE_SECTORS:
        return sector.strip()
    ind = (industry or "").lower()
    if ind:
        for name, words in _RULES:
            if any(w in ind for w in words):
                return name
    sec = (sector or "").strip().lower()
    if sec:
        if sec in _SECTOR_FALLBACK:
            return _SECTOR_FALLBACK[sec]
        for name, words in _RULES:  # e.g. a Screener.in sector like "Banks" or "Pharmaceuticals"
            if any(w in sec for w in words):
                return name
        return sector.strip()[:120]
    return None


def nse_map(db: Session) -> dict[str, str]:
    if time.monotonic() - _cache["t"] < 600:
        return _cache["map"]
    row = db.get(AppSetting, KEY)
    mp = (row.value or {}).get("map", {}) if row and isinstance(row.value, dict) else {}
    _cache.update(t=time.monotonic(), map=mp)
    return mp


def sector_for(db: Session, symbol: str, sector: str | None, industry: str | None) -> str | None:
    return nse_map(db).get(symbol) or canonical(sector, industry)


def parse_industries(text: str) -> dict[str, str]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        return {}
    cols = {k.strip().lower(): k for k in rows[0] if k}
    sym, ind = cols.get("symbol"), cols.get("industry")
    if not sym or not ind:
        return {}
    return {r[sym].strip().upper(): r[ind].strip() for r in rows if r.get(sym) and r.get(ind) and r[ind].strip()}


def due(db: Session) -> bool:
    row = db.get(AppSetting, KEY)
    at = (row.value or {}).get("at") if row and isinstance(row.value, dict) else None
    if not at:
        return True
    from syena.screener.lists import _ts

    return utcnow() - _ts(at) > timedelta(days=REFRESH_DAYS)


def sync(db: Session, get=None) -> dict:
    """Download NSE's sector classification and apply it; translate every other stock's sector to NSE names."""
    from syena.screener.lists import URLS, UA

    import httpx

    get = get or (lambda u: httpx.get(u, headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=25, follow_redirects=True))
    mp: dict[str, str] = {}
    used = []
    for fname in SOURCE_FILES:
        for tpl in URLS:
            try:
                r = get(tpl.format(f=fname))
                r.raise_for_status()
                part = parse_industries(r.text)
            except Exception:
                continue
            if part:
                for k, v in part.items():
                    mp.setdefault(k, v)
                used.append(fname)
                break
    row = db.get(AppSetting, KEY)
    if mp:
        val = {"at": utcnow().isoformat(), "map": mp, "files": used}
        if row is None:
            db.add(AppSetting(key=KEY, value=val, updated_at=utcnow()))
        else:
            row.value, row.updated_at = val, utcnow()
        db.flush()
    elif row is not None and isinstance(row.value, dict):
        mp = row.value.get("map", {})  # NSE unreachable: keep last week's classification
    _cache.update(t=time.monotonic(), map=mp)
    nse = changed = 0
    for s in db.scalars(select(Stock)):
        new = mp.get(s.symbol) or canonical(s.sector, s.industry)
        if s.symbol in mp:
            nse += 1
        if new != s.sector:
            s.sector, changed = new, changed + 1
    db.commit()
    log.info("sectors synced", extra={"nse_classified": nse, "changed": changed, "files": used})
    return {"nse_classified": nse, "changed": changed, "downloaded": bool(used)}
