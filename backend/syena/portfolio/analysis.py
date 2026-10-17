"""Portfolio X-Ray: one view across Zerodha + Angel One + mutual funds.

- allocation: % of total for every holding, by source and by asset class
- look-through: true exposure to each stock = direct holding + share held via each mutual fund
  (fund value × stock's % of fund NAV from the AMC disclosure)
- overlap: for each stock, which of your funds hold it and at what weight; fund-vs-fund overlap
  (sum over common stocks of min(weight_a, weight_b))
- concentration warnings against your limits in Settings
"""
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.db import utcnow
from syena.models import Holding, HoldingSnapshot, IsinMaster, MFSchemeHolding

SOURCES = ("zerodha", "angel", "cas")


def latest_holdings(db: Session) -> tuple[list[Holding], dict]:
    rows, status = [], {}
    for src in SOURCES:
        snap = db.scalar(select(HoldingSnapshot).where(HoldingSnapshot.source == src).order_by(HoldingSnapshot.id.desc()))
        if snap is None:
            status[src] = {"synced_at": None, "age_hours": None, "count": 0}
            continue
        hs = list(db.scalars(select(Holding).where(Holding.snapshot_id == snap.id)))
        status[src] = {"synced_at": snap.taken_at, "age_hours": round((utcnow() - snap.taken_at).total_seconds() / 3600, 1),
                       "count": len(hs), "note": snap.note}
        rows.extend(hs)
    # the same MF can appear in a CAS and in Zerodha Coin: keep the CAS row (it covers all folios)
    cas_isins = {h.isin for h in rows if h.source == "cas" and h.isin}
    rows = [h for h in rows if not (h.source == "zerodha" and h.asset_class == "mf" and h.isin in cas_isins)]
    return rows, status


def scheme_breakdown(db: Session, scheme_key: str) -> tuple[date | None, list[MFSchemeHolding]]:
    as_of = db.scalar(select(func.max(MFSchemeHolding.as_of)).where(MFSchemeHolding.scheme_key == scheme_key))
    if as_of is None:
        return None, []
    return as_of, list(db.scalars(select(MFSchemeHolding).where(MFSchemeHolding.scheme_key == scheme_key, MFSchemeHolding.as_of == as_of)))


def analyse(db: Session) -> dict:
    holdings, status = latest_holdings(db)
    total = sum(h.value for h in holdings) or 0.0
    pct = (lambda v: round(v / total * 100, 3) if total else 0.0)
    master = {m.isin: m for m in db.scalars(select(IsinMaster))}

    allocation = sorted([{
        "source": h.source, "asset_class": h.asset_class, "name": h.name, "symbol": h.symbol, "isin": h.isin,
        "qty": h.qty, "avg_price": h.avg_price, "ltp": h.ltp, "value": round(h.value, 2), "pct": pct(h.value),
        "pnl": round((h.ltp - h.avg_price) * h.qty, 2) if h.avg_price and h.ltp else None,
        "folio": h.folio, "amc": h.amc,
    } for h in holdings], key=lambda r: -r["value"])
    by_source = defaultdict(float)
    by_class = defaultdict(float)
    for h in holdings:
        by_source[h.source] += h.value
        by_class[h.asset_class] += h.value

    # look-through exposure
    exposure: dict[str, dict] = {}

    def slot(key, name, symbol=None):
        e = exposure.setdefault(key, {"key": key, "isin": key if key.startswith("IN") else None, "name": name, "symbol": symbol,
                                      "direct_value": 0.0, "via_funds_value": 0.0, "funds": [], "sector": None})
        if symbol and not e["symbol"]:
            e["symbol"] = symbol
        return e

    funds_meta, fund_weights, missing_disclosures = [], {}, []
    other_mf_value = 0.0
    for h in holdings:
        if h.asset_class == "equity":
            key = h.isin or (h.symbol or h.name)
            slot(key, master.get(key).name if key in master else h.name, h.symbol)["direct_value"] += h.value
            continue
        as_of, parts = scheme_breakdown(db, h.isin or h.name)
        if not parts:
            missing_disclosures.append({"scheme": h.name, "isin": h.isin, "value": round(h.value, 2)})
            other_mf_value += h.value
            continue
        weights = {}
        covered = 0.0
        for p in parts:
            v = h.value * p.pct_nav / 100
            covered += p.pct_nav
            if p.asset_type != "equity" or not p.holding_isin:
                continue
            m = master.get(p.holding_isin)
            e = slot(p.holding_isin, m.name if m else p.holding_name, m.symbol if m else None)
            e["via_funds_value"] += v
            e["sector"] = e["sector"] or p.sector or (m.sector if m else None)
            e["funds"].append({"scheme": h.name, "isin": h.isin, "pct_of_fund": round(p.pct_nav, 3), "value": round(v, 2)})
            weights[p.holding_isin] = weights.get(p.holding_isin, 0) + p.pct_nav
        fund_weights[h.isin or h.name] = (h.name, weights)
        funds_meta.append({"scheme": h.name, "isin": h.isin, "value": round(h.value, 2), "pct": pct(h.value),
                           "disclosure_as_of": as_of.isoformat() if as_of else None, "equity_pct": round(sum(weights.values()), 2),
                           "covered_pct": round(covered, 2), "n_stocks": len(weights)})

    for e in exposure.values():
        m = master.get(e["isin"]) if e["isin"] else None
        e["sector"] = e["sector"] or (m.sector if m else None)
        e["total_value"] = round(e["direct_value"] + e["via_funds_value"], 2)
        e["direct_value"], e["via_funds_value"] = round(e["direct_value"], 2), round(e["via_funds_value"], 2)
        e["total_pct"], e["direct_pct"], e["via_funds_pct"] = pct(e["total_value"]), pct(e["direct_value"]), pct(e["via_funds_value"])
        e["n_funds"] = len(e["funds"])
        e["funds"].sort(key=lambda f: -f["value"])
    look_through = sorted(exposure.values(), key=lambda e: -e["total_value"])

    # fund-vs-fund overlap
    keys = list(fund_weights)
    overlap = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            wa, wb = fund_weights[a][1], fund_weights[b][1]
            common = set(wa) & set(wb)
            if not common:
                continue
            overlap.append({"fund_a": fund_weights[a][0], "fund_b": fund_weights[b][0],
                            "overlap_pct": round(sum(min(wa[k], wb[k]) for k in common), 2), "common_stocks": len(common)})
    overlap.sort(key=lambda r: -r["overlap_pct"])

    sectors = defaultdict(float)
    for e in look_through:
        sectors[e["sector"] or "Unclassified"] += e["total_value"]
    sector_rows = sorted([{"sector": s, "value": round(v, 2), "pct": pct(v)} for s, v in sectors.items()], key=lambda r: -r["value"])

    lim = settings_store.get(db, "portfolio")
    warnings = []
    for e in look_through:
        if e["total_pct"] > lim["max_single_stock_pct"]:
            warnings.append(f"{e['symbol'] or e['name']} is {e['total_pct']:.1f}% of your portfolio "
                            f"({e['direct_pct']:.1f}% direct + {e['via_funds_pct']:.1f}% via {e['n_funds']} funds), "
                            f"above your {lim['max_single_stock_pct']}% limit")
    for s in sector_rows:
        if s["sector"] != "Unclassified" and s["pct"] > lim["max_sector_pct"]:
            warnings.append(f"Sector {s['sector']} is {s['pct']:.1f}% (limit {lim['max_sector_pct']}%)")
    top10 = sum(e["total_pct"] for e in look_through[:10])
    if top10 > lim["max_top10_pct"]:
        warnings.append(f"Top 10 stocks (look-through) are {top10:.1f}% of the portfolio (limit {lim['max_top10_pct']}%)")
    for o in overlap:
        if o["overlap_pct"] >= 50:
            warnings.append(f"{o['fund_a']} and {o['fund_b']} overlap {o['overlap_pct']:.0f}%: they largely hold the same stocks")
    if missing_disclosures:
        warnings.append(f"{len(missing_disclosures)} fund(s) have no AMC disclosure loaded, so their stocks are not in the look-through yet")

    return {
        "total_value": round(total, 2), "as_of": utcnow().isoformat(), "sources": status,
        "by_source": {k: {"value": round(v, 2), "pct": pct(v)} for k, v in by_source.items()},
        "by_asset_class": {k: {"value": round(v, 2), "pct": pct(v)} for k, v in by_class.items()},
        "allocation": allocation, "look_through": look_through, "funds": funds_meta, "fund_overlap": overlap,
        "sectors": sector_rows, "warnings": warnings, "missing_disclosures": missing_disclosures,
        "unexplained_mf_value": round(other_mf_value, 2), "top10_pct": round(top10, 2),
    }
