"""Option chain for NSE index and stock options, from the broker's market data (Angel One or Zerodha Kite).

What it shows for an underlying and expiry: for each strike around the current price, calls and puts with last
price, open interest (OI), today's change in OI, volume, bid/ask, implied volatility (IV) and delta. Plus a summary
read: put-call ratio, max pain, the strikes with the biggest call OI (sellers defend it: resistance) and put OI
(support), the at-the-money straddle and the move it prices in, and OI build-up per strike.

IV and Greeks are worked out here with Black-Scholes (risk-free rate 6.5%, no dividends) from the option's price,
the spot price and the time left to expiry at 15:30 IST, so they're the same whichever broker supplies prices.
Change in OI compares with the last OI Syena saw on an earlier day (stored per contract), so it fills in from the
second day the chain is viewed or refreshed by the worker.

View and analysis only: Syena does not place option orders.
"""
import logging
import math
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.market.calendar import IST
from syena.models import OptionContract, OptionOI

log = logging.getLogger("syena.options")

RISK_FREE = 0.065
INDICES = {  # underlying -> (Angel spot token on NSE, Kite spot key)
    "NIFTY": ("99926000", "NSE:NIFTY 50"),
    "BANKNIFTY": ("99926009", "NSE:NIFTY BANK"),
    "FINNIFTY": ("99926037", "NSE:NIFTY FIN SERVICE"),
    "MIDCPNIFTY": ("99926074", "NSE:NIFTY MID SELECT"),
}
SYNC_KEY = "_option_contracts_synced"


# ---------------------------------------------------------------- Black-Scholes
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_price(s: float, k: float, t: float, vol: float, kind: str, r: float = RISK_FREE) -> float:
    if t <= 0 or vol <= 0:
        return max(0.0, s - k) if kind == "CE" else max(0.0, k - s)
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if kind == "CE":
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def implied_vol(price: float, s: float, k: float, t: float, kind: str, r: float = RISK_FREE) -> float | None:
    """Volatility that makes Black-Scholes match the price (bisection, 1%-500%). None if the price is below intrinsic."""
    if not price or price <= 0 or t <= 0 or s <= 0:
        return None
    intrinsic = max(0.0, s - k * math.exp(-r * t)) if kind == "CE" else max(0.0, k * math.exp(-r * t) - s)
    if price < intrinsic - 0.01:
        return None
    lo, hi = 0.01, 5.0
    if bs_price(s, k, t, hi, kind, r) < price:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(s, k, t, mid, kind, r) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def greeks(s: float, k: float, t: float, vol: float, kind: str, r: float = RISK_FREE) -> dict:
    """delta, gamma, theta (₹ per day), vega (₹ per 1 point of IV)."""
    if t <= 0 or vol <= 0:
        return {"delta": None, "gamma": None, "theta": None, "vega": None}
    st = math.sqrt(t)
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / (vol * st)
    d2 = d1 - vol * st
    gamma = _npdf(d1) / (s * vol * st)
    vega = s * _npdf(d1) * st / 100
    if kind == "CE":
        delta = _ncdf(d1)
        theta = (-s * _npdf(d1) * vol / (2 * st) - r * k * math.exp(-r * t) * _ncdf(d2)) / 365
    else:
        delta = _ncdf(d1) - 1
        theta = (-s * _npdf(d1) * vol / (2 * st) + r * k * math.exp(-r * t) * _ncdf(-d2)) / 365
    return {"delta": round(delta, 3), "gamma": round(gamma, 5), "theta": round(theta, 2), "vega": round(vega, 2)}


def years_to_expiry(expiry: date, now: datetime) -> float:
    end = datetime.combine(expiry, time(15, 30), IST)
    return max((end - now).total_seconds(), 0) / (365 * 24 * 3600)


# ---------------------------------------------------------------- contracts
def _broker(md) -> str:
    return "angel" if hasattr(md, "_token_map") else "kite"


def _angel_rows(md) -> list[dict]:
    from syena.market.angel import SCRIP_MASTER

    r = md.http.get(SCRIP_MASTER, timeout=180)
    r.raise_for_status()
    today = date.today()
    out = []
    for it in r.json():
        if it.get("exch_seg") != "NFO" or it.get("instrumenttype") not in ("OPTIDX", "OPTSTK"):
            continue
        sym = it.get("symbol") or ""
        typ = sym[-2:]
        if typ not in ("CE", "PE"):
            continue
        try:
            exp = datetime.strptime(it["expiry"], "%d%b%Y").date()
            strike = float(it["strike"]) / 100  # Angel lists strikes in paise
        except (KeyError, ValueError):
            continue
        if exp < today:
            continue
        out.append({"broker": "angel", "token": str(it["token"]), "tradingsymbol": sym, "underlying": it.get("name") or "",
                    "expiry": exp, "strike": strike, "option_type": typ, "lot_size": int(float(it.get("lotsize") or 1)),
                    "kind": "index" if it["instrumenttype"] == "OPTIDX" else "stock"})
    return out


def _kite_rows(md) -> list[dict]:
    today = date.today()
    out = []
    for it in md._kite().instruments("NFO"):
        if it.get("instrument_type") not in ("CE", "PE") or not it.get("expiry"):
            continue
        exp = it["expiry"] if isinstance(it["expiry"], date) else date.fromisoformat(str(it["expiry"])[:10])
        if exp < today:
            continue
        name = it.get("name") or ""
        out.append({"broker": "kite", "token": str(it["instrument_token"]), "tradingsymbol": it["tradingsymbol"], "underlying": name,
                    "expiry": exp, "strike": float(it["strike"]), "option_type": it["instrument_type"],
                    "lot_size": int(it.get("lot_size") or 1), "kind": "index" if name in INDICES else "stock"})
    return out


def sync_contracts(db: Session, md) -> int:
    """Replace the option list with today's from the broker (one download, bulk insert)."""
    rows = _angel_rows(md) if _broker(md) == "angel" else _kite_rows(md)
    if not rows:
        raise ValueError("the broker returned no option contracts")
    db.execute(delete(OptionContract))
    for i in range(0, len(rows), 5000):
        db.execute(insert(OptionContract), rows[i:i + 5000])
    from syena.models import AppSetting

    row = db.get(AppSetting, SYNC_KEY) or AppSetting(key=SYNC_KEY)
    row.value, row.updated_at = {"date": date.today().isoformat(), "broker": _broker(md), "count": len(rows)}, utcnow()
    db.merge(row)
    db.commit()
    log.info("option contracts synced", extra={"count": len(rows)})
    return len(rows)


def ensure_contracts(db: Session, md) -> None:
    from syena.models import AppSetting

    row = db.get(AppSetting, SYNC_KEY)
    v = (row.value or {}) if row else {}
    if v.get("date") != date.today().isoformat() or v.get("broker") != _broker(md):
        sync_contracts(db, md)


def underlyings(db: Session) -> dict:
    names = sorted({u for (u,) in db.execute(select(OptionContract.underlying).distinct())})
    idx = [n for n in INDICES if n in names] or list(INDICES)
    return {"indices": idx, "stocks": [n for n in names if n not in INDICES]}


def expiries(db: Session, underlying: str) -> list[date]:
    return sorted({e for (e,) in db.execute(select(OptionContract.expiry).where(OptionContract.underlying == underlying).distinct())})


# ---------------------------------------------------------------- prices
def spot(md, underlying: str) -> float | None:
    if underlying in INDICES:
        a_tok, k_key = INDICES[underlying]
        if _broker(md) == "angel":
            q = md._quote("LTP", {"NSE": [a_tok]})
            return float(q[0]["ltp"]) if q and q[0].get("ltp") is not None else None
        d = md._kite().ltp([k_key]).get(k_key)
        return float(d["last_price"]) if d else None
    return md.ltp([underlying]).get(underlying)


def quotes(md, contracts: list[OptionContract]) -> dict[str, dict]:
    """token -> {ltp, prev_close, oi, volume, bid, ask}."""
    out: dict[str, dict] = {}
    if not contracts:
        return out
    if _broker(md) == "angel":
        for q in md._quote("FULL", {"NFO": [c.token for c in contracts]}):
            depth = q.get("depth") or {}
            bid = (depth.get("buy") or [{}])[0].get("price")
            ask = (depth.get("sell") or [{}])[0].get("price")
            out[str(q.get("symbolToken"))] = {"ltp": q.get("ltp"), "prev_close": q.get("close"), "oi": q.get("opnInterest"),
                                              "volume": q.get("tradeVolume"), "bid": bid or None, "ask": ask or None}
        return out
    kite = md._kite()
    by_key = {f"NFO:{c.tradingsymbol}": c.token for c in contracts}
    keys = list(by_key)
    for i in range(0, len(keys), 250):
        for k, v in kite.quote(keys[i:i + 250]).items():
            depth = v.get("depth") or {}
            out[by_key[k]] = {"ltp": v.get("last_price"), "prev_close": (v.get("ohlc") or {}).get("close"), "oi": v.get("oi"),
                              "volume": v.get("volume"), "bid": ((depth.get("buy") or [{}])[0].get("price")) or None,
                              "ask": ((depth.get("sell") or [{}])[0].get("price")) or None}
    return out


def _record_oi(db: Session, q: dict[str, dict], day: date) -> dict[str, float]:
    """Store today's OI per contract; return the previous day's OI for each (for the change)."""
    toks = list(q)
    prev: dict[str, float] = {}
    for r in db.scalars(select(OptionOI).where(OptionOI.token.in_(toks), OptionOI.day < day).order_by(OptionOI.day)):
        prev[r.token] = r.oi  # ordered by day: the latest earlier day wins
    today = {r.token: r for r in db.scalars(select(OptionOI).where(OptionOI.token.in_(toks), OptionOI.day == day))}
    for t, v in q.items():
        if v.get("oi") is None:
            continue
        row = today.get(t)
        if row is None:
            db.add(OptionOI(token=t, day=day, oi=float(v["oi"]), ltp=v.get("ltp")))
        else:
            row.oi, row.ltp = float(v["oi"]), v.get("ltp")
    db.commit()
    return prev


# ---------------------------------------------------------------- chain + analytics
def buildup(price_change: float | None, oi_change: float | None) -> str | None:
    if price_change is None or oi_change is None or oi_change == 0:
        return None
    if oi_change > 0:
        return "long build-up" if price_change > 0 else "short build-up"
    return "short covering" if price_change > 0 else "long unwinding"


def max_pain(rows: list[dict]) -> float | None:
    """The expiry price at which option buyers' total payout (what writers lose) is smallest."""
    strikes = [r["strike"] for r in rows]
    if not strikes:
        return None
    best, best_pay = None, None
    for x in strikes:
        pay = sum((r["ce"] or {}).get("oi", 0) * max(0.0, x - r["strike"]) + (r["pe"] or {}).get("oi", 0) * max(0.0, r["strike"] - x)
                  for r in rows)
        if best_pay is None or pay < best_pay:
            best, best_pay = x, pay
    return best


def chain(db: Session, md, underlying: str, expiry: date | None = None, strikes: int = 15, now: datetime | None = None) -> dict:
    now = now or utcnow()
    underlying = underlying.strip().upper()
    exps = expiries(db, underlying)
    if not exps:
        raise ValueError(f"no listed options for {underlying}")
    expiry = expiry if expiry in exps else exps[0]
    s = spot(md, underlying)
    contracts = list(db.scalars(select(OptionContract).where(OptionContract.underlying == underlying, OptionContract.expiry == expiry)))
    all_strikes = sorted({c.strike for c in contracts})
    if not all_strikes:
        raise ValueError(f"no contracts for {underlying} {expiry}")
    ref = s or all_strikes[len(all_strikes) // 2]
    atm = min(all_strikes, key=lambda k: abs(k - ref))
    i = all_strikes.index(atm)
    window = all_strikes[max(0, i - strikes): i + strikes + 1]
    picked = [c for c in contracts if c.strike in set(window)]
    q = quotes(md, picked)
    day = now.astimezone(IST).date()
    prev_oi = _record_oi(db, q, day)
    t = years_to_expiry(expiry, now)
    rows: dict[float, dict] = {k: {"strike": k, "ce": None, "pe": None} for k in window}
    lot = picked[0].lot_size if picked else 1
    for c in picked:
        v = q.get(c.token) or {}
        ltp, oi = v.get("ltp"), v.get("oi")
        mid = (v["bid"] + v["ask"]) / 2 if v.get("bid") and v.get("ask") else None
        iv = implied_vol(mid or ltp, s, c.strike, t, c.option_type) if s and (mid or ltp) else None
        g = greeks(s, c.strike, t, iv, c.option_type) if iv and s else {"delta": None, "gamma": None, "theta": None, "vega": None}
        p_chg = (ltp - v["prev_close"]) if ltp is not None and v.get("prev_close") else None
        oi_chg = (oi - prev_oi[c.token]) if oi is not None and c.token in prev_oi else None
        rows[c.strike]["ce" if c.option_type == "CE" else "pe"] = {
            "symbol": c.tradingsymbol, "ltp": ltp, "change": round(p_chg, 2) if p_chg is not None else None,
            "oi": oi or 0, "oi_change": oi_chg, "volume": v.get("volume"), "bid": v.get("bid"), "ask": v.get("ask"),
            "iv": round(iv * 100, 1) if iv else None, **g, "buildup": buildup(p_chg, oi_chg)}
    table = [rows[k] for k in window]
    ce_oi = sum((r["ce"] or {}).get("oi", 0) for r in table)
    pe_oi = sum((r["pe"] or {}).get("oi", 0) for r in table)
    top_ce = max(table, key=lambda r: (r["ce"] or {}).get("oi", 0))
    top_pe = max(table, key=lambda r: (r["pe"] or {}).get("oi", 0))
    atm_row = rows[atm]
    straddle = ((atm_row["ce"] or {}).get("ltp") or 0) + ((atm_row["pe"] or {}).get("ltp") or 0)
    atm_iv = [x for x in ((atm_row["ce"] or {}).get("iv"), (atm_row["pe"] or {}).get("iv")) if x]
    pcr = round(pe_oi / ce_oi, 2) if ce_oi else None
    mp = max_pain(table)
    read = []
    if pcr is not None:
        read.append(f"PCR {pcr}: " + ("more puts written than calls (traders expect support)" if pcr > 1.2 else
                                      "more calls written than puts (traders expect a ceiling)" if pcr < 0.8 else "balanced"))
    if (top_ce["ce"] or {}).get("oi"):
        read.append(f"biggest call OI at {top_ce['strike']:g}: likely resistance")
    if (top_pe["pe"] or {}).get("oi"):
        read.append(f"biggest put OI at {top_pe['strike']:g}: likely support")
    if straddle and s:
        read.append(f"ATM straddle ₹{straddle:,.1f} prices in a move of about ±{straddle / s * 100:.1f}% by expiry")
    if mp and s:
        read.append(f"max pain {mp:g} ({(mp / s - 1) * 100:+.1f}% from spot)")
    return {"underlying": underlying, "expiry": expiry.isoformat(), "expiries": [e.isoformat() for e in exps], "spot": s,
            "atm": atm, "lot_size": lot, "days_to_expiry": round(t * 365, 1), "at": now.isoformat(), "rows": table,
            "summary": {"pcr": pcr, "max_pain": mp, "call_oi": ce_oi, "put_oi": pe_oi, "resistance": top_ce["strike"] if (top_ce["ce"] or {}).get("oi") else None,
                        "support": top_pe["strike"] if (top_pe["pe"] or {}).get("oi") else None, "straddle": round(straddle, 2) or None,
                        "expected_move_pct": round(straddle / s * 100, 2) if straddle and s else None,
                        "atm_iv": round(sum(atm_iv) / len(atm_iv), 1) if atm_iv else None, "read": read},
            "oi_change_note": None if prev_oi else "Change in OI appears from the second day: Syena compares with the OI it saw on an earlier day."}


# ---------------------------------------------------------------- prices for option trades
def split_option_symbols(db: Session, symbols: list[str]) -> tuple[dict[str, OptionContract], list[str]]:
    """Which of these symbols are option contracts (by trading symbol), and which are shares."""
    maybe = [s for s in symbols if s.endswith(("CE", "PE")) and any(ch.isdigit() for ch in s)]
    found = {c.tradingsymbol: c for c in db.scalars(select(OptionContract).where(OptionContract.tradingsymbol.in_(maybe)))} if maybe else {}
    return found, [s for s in symbols if s not in found]


def option_ltp(md, contracts: dict[str, OptionContract]) -> dict[str, float]:
    if not contracts:
        return {}
    q = quotes(md, list(contracts.values()))
    by_tok = {c.token: sym for sym, c in contracts.items()}
    return {by_tok[t]: float(v["ltp"]) for t, v in q.items() if t in by_tok and v.get("ltp") is not None}
