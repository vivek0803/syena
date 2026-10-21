"""Article -> candidate NSE symbols. Deterministic candidate generation; the triage model picks from these."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.models import Instrument

# Common names/aliases for NIFTY 50 constituents (used when the instrument master lacks a friendly name).
ALIASES: dict[str, list[str]] = {
    "RELIANCE": ["reliance industries", "reliance", "ril", "jio"], "TCS": ["tata consultancy", "tcs"],
    "HDFCBANK": ["hdfc bank"], "ICICIBANK": ["icici bank"], "INFY": ["infosys"], "BHARTIARTL": ["bharti airtel", "airtel"],
    "SBIN": ["state bank of india", "sbi"], "ITC": ["itc"], "LT": ["larsen & toubro", "larsen and toubro", "l&t"],
    "HINDUNILVR": ["hindustan unilever", "hul"], "KOTAKBANK": ["kotak mahindra bank", "kotak bank"],
    "AXISBANK": ["axis bank"], "BAJFINANCE": ["bajaj finance"], "BAJAJFINSV": ["bajaj finserv"], "MARUTI": ["maruti suzuki", "maruti"],
    "SUNPHARMA": ["sun pharma", "sun pharmaceutical"], "HCLTECH": ["hcl technologies", "hcltech", "hcl tech"],
    "TITAN": ["titan company", "titan"], "ULTRACEMCO": ["ultratech cement", "ultratech"], "ASIANPAINT": ["asian paints"],
    "NTPC": ["ntpc"], "POWERGRID": ["power grid"], "ONGC": ["ongc", "oil and natural gas"], "TATAMOTORS": ["tata motors"],
    "TATASTEEL": ["tata steel"], "M&M": ["mahindra & mahindra", "mahindra and mahindra", "m&m"], "WIPRO": ["wipro"],
    "NESTLEIND": ["nestle india"], "JSWSTEEL": ["jsw steel"], "ADANIENT": ["adani enterprises"], "ADANIPORTS": ["adani ports"],
    "COALINDIA": ["coal india"], "TECHM": ["tech mahindra"], "GRASIM": ["grasim"], "HINDALCO": ["hindalco"],
    "CIPLA": ["cipla"], "DRREDDY": ["dr reddy", "dr. reddy"], "EICHERMOT": ["eicher motors", "royal enfield"],
    "BRITANNIA": ["britannia"], "HEROMOTOCO": ["hero motocorp"], "APOLLOHOSP": ["apollo hospitals"], "BPCL": ["bpcl", "bharat petroleum"],
    "INDUSINDBK": ["indusind bank"], "TATACONSUM": ["tata consumer"], "SBILIFE": ["sbi life"], "HDFCLIFE": ["hdfc life"],
    "BAJAJ-AUTO": ["bajaj auto"], "SHRIRAMFIN": ["shriram finance"], "TRENT": ["trent"], "BEL": ["bharat electronics"],
}
_SUFFIX = re.compile(r"\b(limited|ltd|industries|india|corporation|corp|company|co)\b\.?", re.I)


def _norm(t: str) -> str:
    return " " + re.sub(r"[^a-z0-9& ]", " ", t.lower()) + " "


# NSE series suffixes on trading symbols ("GULFPETRO-BE" = trade-to-trade segment of GULFPETRO). Prices, screens and
# orders all use the plain symbol, so news maps to that. BZ (suspended/non-compliant) stocks are left out entirely.
SERIES = {"BE", "BL", "IL", "E1", "BT"}
SKIP_SERIES = {"BZ", "SM", "ST"}  # suspended (BZ) and SME-platform (SM/ST) stocks: no price data, can't be traded here
_cache: dict = {"t": 0.0, "v": None}


def base_symbol(sym: str) -> str | None:
    head, _, tail = sym.rpartition("-")
    if head and tail in SKIP_SERIES:
        return None
    return head if head and tail in SERIES else sym


def instrument_aliases(db: Session) -> dict[str, list[str]]:
    import time

    if _cache["v"] is not None and time.monotonic() - _cache["t"] < 600:
        return _cache["v"]
    from syena.models import Stock

    out = {k: list(v) for k, v in ALIASES.items()}

    def add(sym: str, name: str | None) -> None:
        base = base_symbol(sym)
        if not base:
            return
        names = out.setdefault(base, [])
        if name:
            core = " ".join(_SUFFIX.sub(" ", name.lower()).split())
            core = " ".join(re.sub(r"[^a-z0-9& ]", " ", core).split())
            if len(core) >= 4 and core not in names:
                names.append(core)

    for sym, name in db.execute(select(Instrument.tradingsymbol, Instrument.name)
                                .where(Instrument.exchange == "NSE", Instrument.instrument_type == "EQ")):
        add(sym, name)
    for sym, name in db.execute(select(Stock.symbol, Stock.name).where(Stock.active.is_(True))):  # Angel users: names from the screener
        add(sym, name)
    # only propose stocks Syena can price and trade: the stock list (built from the price source) when it exists
    tradeable = set(db.scalars(select(Stock.symbol).where(Stock.active.is_(True))))
    if tradeable:
        out = {k: v for k, v in out.items() if k in tradeable}
    _cache.update(t=time.monotonic(), v=out)
    return out


def candidates(db: Session, text: str, extra: list[str] | None = None, max_n: int = 15) -> dict[str, str]:
    """symbol -> matched alias. Always includes `extra` (watchlist + held stocks) only if mentioned."""
    nt = _norm(text)
    found: dict[str, str] = {}
    for sym, names in instrument_aliases(db).items():
        if re.search(rf"\b{re.escape(sym.lower())}\b", nt) and len(sym) >= 3:
            found[sym] = sym
            continue
        for n in names:
            if f" {n} " in nt:
                found[sym] = n
                break
        if len(found) >= max_n:
            break
    for sym in extra or []:
        if sym not in found and any(f" {a} " in nt for a in ALIASES.get(sym, [sym.lower()])):
            found[sym] = sym
    return found
