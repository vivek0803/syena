"""Ready-made stock lists for picking an algo's stocks: NSE indices, market-cap groups, sectors, screens, search.

Index members come from NSE's published constituent files (niftyindices.com, with nseindia.com as backup),
cached for a week. Market-cap groups follow SEBI's ranking: large = top 100, mid = 101-250, small = 251-500.
"""
import csv
import io
import logging
from datetime import timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.models import AppSetting, Screen, Stock
from syena.screener import screens

log = logging.getLogger("syena.lists")

INDICES = {  # key -> (label, NSE file name)
    "nifty50": ("Nifty 50", "ind_nifty50list"),
    "niftynext50": ("Nifty Next 50", "ind_niftynext50list"),
    "nifty100": ("Nifty 100", "ind_nifty100list"),
    "nifty200": ("Nifty 200", "ind_nifty200list"),
    "nifty500": ("Nifty 500", "ind_nifty500list"),
    "midcap100": ("Nifty Midcap 100", "ind_niftymidcap100list"),
    "midcap150": ("Nifty Midcap 150", "ind_niftymidcap150list"),
    "smallcap100": ("Nifty Smallcap 100", "ind_niftysmallcap100list"),
    "smallcap250": ("Nifty Smallcap 250", "ind_niftysmallcap250list"),
    "microcap250": ("Nifty Microcap 250", "ind_niftymicrocap250_list"),
    "totalmarket": ("Nifty Total Market (~750)", "ind_niftytotalmarket_list"),
    # sectoral
    "bank": ("Nifty Bank", "ind_niftybanklist"),
    "privatebank": ("Nifty Private Bank", "ind_nifty_privatebanklist"),
    "psubank": ("Nifty PSU Bank", "ind_niftypsubanklist"),
    "finance": ("Nifty Financial Services", "ind_niftyfinancelist"),
    "it": ("Nifty IT", "ind_niftyitlist"),
    "pharma": ("Nifty Pharma", "ind_niftypharmalist"),
    "healthcare": ("Nifty Healthcare", "ind_niftyhealthcarelist"),
    "auto": ("Nifty Auto", "ind_niftyautolist"),
    "fmcg": ("Nifty FMCG", "ind_niftyfmcglist"),
    "metal": ("Nifty Metal", "ind_niftymetallist"),
    "realty": ("Nifty Realty", "ind_niftyrealtylist"),
    "energy": ("Nifty Energy", "ind_niftyenergylist"),
    "oilgas": ("Nifty Oil & Gas", "ind_niftyoilgaslist"),
    "media": ("Nifty Media", "ind_niftymedialist"),
    "consumerdurables": ("Nifty Consumer Durables", "ind_niftyconsumerdurableslist"),
    # thematic
    "infra": ("Nifty Infrastructure", "ind_niftyinfralist"),
    "pse": ("Nifty PSE (govt companies)", "ind_niftypselist"),
    "commodities": ("Nifty Commodities", "ind_niftycommoditieslist"),
    "consumption": ("Nifty India Consumption", "ind_niftyconsumptionlist"),
}
SECTORAL = {"bank", "privatebank", "psubank", "finance", "it", "pharma", "healthcare", "auto", "fmcg", "metal", "realty",
            "energy", "oilgas", "media", "consumerdurables"}
THEMATIC = {"infra", "pse", "commodities", "consumption"}
URLS = ("https://www.niftyindices.com/IndexConstituent/{f}.csv",
        "https://nsearchives.nseindia.com/content/indices/{f}.csv",
        "https://archives.nseindia.com/content/indices/{f}.csv")
CAPS = {"large": ("Large caps (top 100 by market cap)", 0, 100),
        "mid": ("Mid caps (101–250)", 100, 250),
        "small": ("Small caps (251–500)", 250, 500)}
CACHE_DAYS = 7
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


class ListError(Exception):
    pass


def parse_constituents(text: str) -> list[str]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        return []
    key = next((k for k in rows[0] if k and k.strip().lower() == "symbol"), None)
    if key is None:
        raise ListError("NSE file has no Symbol column")
    series = next((k for k in rows[0] if k and k.strip().lower() == "series"), None)
    return [r[key].strip().upper() for r in rows if r.get(key) and (not series or (r[series] or "EQ").strip() in ("EQ", "BE", ""))]


def _download(fname: str, get=None) -> list[str]:
    get = get or (lambda u: httpx.get(u, headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=20, follow_redirects=True))
    errors = []
    for tpl in URLS:
        url = tpl.format(f=fname)
        try:
            r = get(url)
            r.raise_for_status()
            syms = parse_constituents(r.text)
            if syms:
                return syms
            errors.append(f"{url}: empty")
        except Exception as e:
            errors.append(f"{url}: {e}")
    raise ListError("Couldn't download the index list from NSE (" + "; ".join(errors)[:300] + ")")


def index_members(db: Session, key: str, get=None) -> list[str]:
    if key not in INDICES:
        raise ListError(f"unknown index {key}")
    ck = f"_index_{key}"
    row = db.get(AppSetting, ck)
    cached = row.value if row and isinstance(row.value, dict) else None
    if cached and cached.get("at") and utcnow() - _ts(cached["at"]) < timedelta(days=CACHE_DAYS):
        return cached["symbols"]
    try:
        syms = _download(INDICES[key][1], get)
    except ListError:
        if cached:  # NSE unreachable: an older list is better than none
            log.warning("index download failed, using cached list", extra={"index": key})
            return cached["symbols"]
        raise
    val = {"at": utcnow().isoformat(), "symbols": syms}
    if row is None:
        db.add(AppSetting(key=ck, value=val, updated_at=utcnow()))
    else:
        row.value, row.updated_at = val, utcnow()
    db.commit()
    return syms


def _ts(s: str):
    from datetime import datetime

    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=utcnow().tzinfo)


def _by_cap(db: Session) -> list[dict]:
    rows = [r for r in screens.rows_for_day(db, screens.latest_day(db)) if r.get("market_cap")]
    return sorted(rows, key=lambda r: -r["market_cap"])


def cap_group(db: Session, key: str) -> list[str]:
    if key not in CAPS:
        raise ListError(f"unknown group {key}")
    _, lo, hi = CAPS[key]
    ranked = _by_cap(db)
    if not ranked:
        raise ListError("Market caps aren't known yet: load prices and company figures on the Screener page first.")
    return [r["symbol"] for r in ranked[lo:hi]]


def sector(db: Session, name: str) -> list[str]:
    syms = {s for s in db.scalars(select(Stock.symbol).where(Stock.active.is_(True), Stock.sector == name))}
    order = {r["symbol"]: i for i, r in enumerate(_by_cap(db))}
    return sorted(syms, key=lambda s: order.get(s, 10**6))


def catalog(db: Session) -> dict:
    from sqlalchemy import func

    from syena.screener import sectors as sec

    if sec.due(db) and db.scalar(select(func.count()).select_from(Stock)):
        _sync_sectors_in_background()
    counts = db.execute(select(Stock.sector, func.count()).where(Stock.active.is_(True), Stock.sector.is_not(None))
                        .group_by(Stock.sector)).all()
    group = lambda k: "Sectoral index" if k in SECTORAL else "Thematic index" if k in THEMATIC else "Index"  # noqa: E731
    return {
        "indices": [{"key": k, "label": v[0], "group": group(k)} for k, v in INDICES.items()],
        "caps": [{"key": k, "label": v[0]} for k, v in CAPS.items()],
        "sectors": [{"key": name, "label": name, "count": n} for name, n in sorted(counts, key=lambda x: x[0])],
        "unclassified": db.scalar(select(func.count()).select_from(Stock).where(Stock.active.is_(True), Stock.sector.is_(None))),
        "screens": [{"key": str(s.id), "label": s.name, "count": len(s.members or [])} for s in db.scalars(select(Screen).order_by(Screen.id))],
    }


_sector_job = {"running": False}


def _sync_sectors_in_background() -> None:
    import threading

    if _sector_job["running"]:
        return
    _sector_job["running"] = True

    def run():
        from syena.db import SessionLocal
        from syena.screener import sectors as sec

        try:
            with SessionLocal() as s:
                sec.sync(s)
        except Exception as e:
            log.warning("sector sync failed", extra={"error": str(e)})
        finally:
            _sector_job["running"] = False

    threading.Thread(target=run, daemon=True).start()


def resolve(db: Session, kind: str, key: str) -> list[str]:
    if kind == "index":
        syms = index_members(db, key)
    elif kind == "cap":
        return cap_group(db, key)
    elif kind == "sector":
        return sector(db, key)
    elif kind == "screen":
        sc = db.get(Screen, int(key)) if key.isdigit() else None
        if sc is None:
            raise ListError("screen not found")
        syms = list(sc.members or [])
    else:
        raise ListError(f"unknown list kind {kind}")
    order = {r["symbol"]: i for i, r in enumerate(_by_cap(db))}  # biggest first, so a cap keeps the largest
    return sorted(syms, key=lambda s: order.get(s, 10**6 + syms.index(s)))


def search(db: Session, q: str, limit: int = 20) -> list[dict]:
    q = q.strip().upper()
    if not q:
        return []
    rows = list(db.scalars(select(Stock).where(Stock.active.is_(True), or_(Stock.symbol.ilike(f"{q}%"), Stock.name.ilike(f"%{q}%")))
                           .limit(200)))
    rows.sort(key=lambda s: (s.symbol != q, not s.symbol.startswith(q), s.symbol))
    if not rows:  # stock list not built yet: fall back to the broker's instrument list
        try:
            from syena.market import source
            from syena.screener import data

            syms = data.universe_from_md(db, source.get_market_data(db))
            return [{"symbol": s, "name": None} for s in sorted(syms) if s.startswith(q)][:limit]
        except Exception:
            return []
    return [{"symbol": s.symbol, "name": s.name} for s in rows[:limit]]
