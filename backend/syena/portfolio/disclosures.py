"""AMC monthly portfolio disclosures -> underlying holdings of each scheme.

Every AMC publishes a monthly Excel of each scheme's holdings, in slightly different layouts. Instead of
one adapter per AMC, this parser finds the header row (an ISIN column + a '% to NAV' column + a name column)
in every sheet, reads rows until the totals, and detects the scheme name from the rows above the header.
Upload the file (or give its URL) on the Portfolio page; schemes are matched to your holdings by name.
"""
import difflib
import io
import logging
import re
from datetime import date

import httpx
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from syena.models import Holding, HoldingSnapshot, IsinMaster, MFSchemeHolding

log = logging.getLogger("syena.portfolio")

ISIN_RE = re.compile(r"^IN[0-9A-Z]{10}$")
PCT_RE = re.compile(r"%\s*(to|of)?\s*(net\s*assets?|nav|aum|n\.?a\.?v)|percentage|% of portfolio|% net assets", re.I)
NAME_RE = re.compile(r"name of (the )?(instrument|issuer|security)|instrument|security name|company|issuer|scrip", re.I)
SECTOR_RE = re.compile(r"industry|sector|rating", re.I)


def asset_type(isin: str | None) -> str:
    if not isin:
        return "other"
    if isin.startswith("INF"):
        return "fund"
    if not isin.startswith("INE"):
        return "debt"  # government securities / T-bills / state loans (IN0..., IN1...)
    code = isin[7:9]  # security-type code: 01 = equity shares
    return "equity" if code == "01" else "other" if code in ("02", "03", "04", "05", "06", "09") else "debt"


def _find_header(df: pd.DataFrame) -> tuple[int, dict] | None:
    for i in range(min(len(df), 40)):
        cells = [str(x) for x in df.iloc[i].tolist()]
        cols = {}
        for j, c in enumerate(cells):
            if "isin" in c.lower() and "isin" not in cols:
                cols["isin"] = j
            elif PCT_RE.search(c) and "pct" not in cols:
                cols["pct"] = j
            elif NAME_RE.search(c) and "name" not in cols:
                cols["name"] = j
            elif SECTOR_RE.search(c) and "sector" not in cols:
                cols["sector"] = j
        if {"isin", "pct", "name"} <= cols.keys():
            return i, cols
    return None


def _scheme_name(df: pd.DataFrame, header_row: int, sheet: str) -> str:
    for i in range(header_row):
        for x in df.iloc[i].tolist():
            s = str(x).strip()
            if re.search(r"\b(fund|scheme|plan|etf|index)\b", s, re.I) and 8 < len(s) < 200 and "portfolio" not in s.lower()[:12]:
                return re.sub(r"\s+", " ", re.sub(r"(?i)^(scheme name|portfolio of|monthly portfolio( statement)?( of)?)\s*[:\-]?\s*", "", s))
    return sheet


def parse_workbook(content: bytes) -> list[dict]:
    """Returns [{scheme_name, holdings: [{isin, name, pct_nav, sector, asset_type}]}]."""
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=object)
    out = []
    for sheet, df in sheets.items():
        found = _find_header(df)
        if not found:
            continue
        hr, cols = found
        holdings = []
        for _, row in df.iloc[hr + 1:].iterrows():
            isin = str(row.iloc[cols["isin"]]).strip().upper()
            if not ISIN_RE.match(isin):
                continue
            try:
                pct = float(str(row.iloc[cols["pct"]]).replace("%", "").strip())
            except ValueError:
                continue
            holdings.append({"isin": isin, "name": str(row.iloc[cols["name"]]).strip()[:255], "pct_nav": pct,
                             "sector": str(row.iloc[cols["sector"]]).strip()[:128] if "sector" in cols and pd.notna(row.iloc[cols["sector"]]) else None,
                             "asset_type": asset_type(isin)})
        if holdings:
            total = sum(h["pct_nav"] for h in holdings)
            if total <= 1.5:  # this AMC reports fractions (0.0523) instead of percent (5.23)
                for h in holdings:
                    h["pct_nav"] *= 100
            out.append({"scheme_name": _scheme_name(df, hr, sheet), "sheet": sheet, "holdings": holdings})
    return out


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b(direct|regular|growth|idcw|dividend|plan|option|reinvestment|payout|fund|scheme|the)\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


def user_schemes(db: Session) -> list[Holding]:
    out, seen = [], set()
    for src in ("cas", "zerodha"):
        snap = db.scalar(select(HoldingSnapshot).where(HoldingSnapshot.source == src).order_by(HoldingSnapshot.id.desc()))
        if snap:
            for h in db.scalars(select(Holding).where(Holding.snapshot_id == snap.id, Holding.asset_class == "mf")):
                if h.isin not in seen:
                    seen.add(h.isin)
                    out.append(h)
    return out


def match(parsed: list[dict], schemes: list[Holding], cutoff: float = 0.6) -> list[tuple[dict, Holding | None, float]]:
    res = []
    for p in parsed:
        best, score = None, 0.0
        for h in schemes:
            r = difflib.SequenceMatcher(None, _norm(p["scheme_name"]), _norm(h.name)).ratio()
            if r > score:
                best, score = h, r
        res.append((p, best if score >= cutoff else None, round(score, 3)))
    return res


def store(db: Session, scheme_key: str, scheme_name: str, as_of: date, holdings: list[dict]) -> int:
    db.execute(delete(MFSchemeHolding).where(MFSchemeHolding.scheme_key == scheme_key, MFSchemeHolding.as_of == as_of))
    for h in holdings:
        db.add(MFSchemeHolding(scheme_key=scheme_key, scheme_name=scheme_name[:255], as_of=as_of, holding_isin=h["isin"],
                               holding_name=h["name"], pct_nav=h["pct_nav"], sector=h["sector"], asset_type=h["asset_type"]))
        if h["asset_type"] == "equity":
            m = db.get(IsinMaster, h["isin"])
            if m is None:
                db.add(IsinMaster(isin=h["isin"], name=h["name"], sector=h["sector"]))
            elif h["sector"] and not m.sector:
                m.sector = h["sector"]
            db.flush()
    db.commit()
    return len(holdings)


def import_disclosure(db: Session, content: bytes, as_of: date, manual_map: dict[str, str] | None = None) -> dict:
    """manual_map: {sheet or scheme_name: holding ISIN} to override automatic name matching."""
    parsed = parse_workbook(content)
    if not parsed:
        raise ValueError("no scheme portfolio tables found (need ISIN, name and '% to NAV' columns)")
    schemes = user_schemes(db)
    by_isin = {h.isin: h for h in schemes}
    report = []
    for p, h, score in match(parsed, schemes):
        key = (manual_map or {}).get(p["sheet"]) or (manual_map or {}).get(p["scheme_name"])
        if key and key in by_isin:
            h, score = by_isin[key], 1.0
        if h is None:
            report.append({"scheme": p["scheme_name"], "matched": None, "score": score, "stored": 0})
            continue
        n = store(db, h.isin or h.name, h.name, as_of, p["holdings"])
        report.append({"scheme": p["scheme_name"], "matched": h.name, "isin": h.isin, "score": score, "stored": n})
    log.info("AMC disclosure imported", extra={"schemes": len(parsed), "matched": sum(1 for r in report if r["matched"])})
    return {"schemes_in_file": len(parsed), "matched": sum(1 for r in report if r["matched"]), "details": report}


def fetch_url(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed")
    r = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Syena/1.0"})
    r.raise_for_status()
    if len(r.content) > 25_000_000:
        raise ValueError("file too large")
    return r.content
