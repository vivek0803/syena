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
# Brand / short names the press uses that differ from the registered name (the instrument master only knows the
# registered one, so "Zomato" or "HAL" would otherwise map to nothing and the news would be dropped).
ALIASES.update({
    "ETERNAL": ["zomato", "eternal", "blinkit"], "PAYTM": ["paytm", "one 97"], "NYKAA": ["nykaa", "fsn e-commerce", "fsn ecommerce"],
    "POLICYBZR": ["policybazaar", "pb fintech"], "SWIGGY": ["swiggy"], "OLAELEC": ["ola electric"], "JIOFIN": ["jio financial"],
    "LICI": ["lic", "life insurance corporation"], "HAL": ["hal", "hindustan aeronautics"], "BEL": ["bel", "bharat electronics"],
    "BHEL": ["bhel", "bharat heavy electricals"], "IRCTC": ["irctc"], "IRFC": ["irfc"], "RVNL": ["rvnl", "rail vikas"],
    "IREDA": ["ireda"], "SAIL": ["sail", "steel authority"], "GAIL": ["gail"], "IOC": ["indian oil", "ioc", "iocl"],
    "HINDPETRO": ["hpcl", "hindustan petroleum"], "MAZDOCK": ["mazagon dock"], "COCHINSHIP": ["cochin shipyard"],
    "BDL": ["bharat dynamics"], "NHPC": ["nhpc"], "SJVN": ["sjvn"], "PFC": ["pfc", "power finance corporation"],
    "RECLTD": ["rec ltd", "rec limited", "rural electrification"], "LTIM": ["ltimindtree", "lti mindtree"],
    "LTTS": ["l&t technology services", "ltts"], "DMART": ["dmart", "d-mart", "avenue supermarts"], "MOTHERSON": ["motherson"],
    "TATAPOWER": ["tata power"], "TATACHEM": ["tata chemicals"], "TATAELXSI": ["tata elxsi"], "TATACOMM": ["tata communications"],
    "TMPV": ["tata motors passenger"], "TMCV": ["tata motors commercial"], "ZYDUSLIFE": ["zydus"], "MCX": ["mcx"],
    "CDSL": ["cdsl"], "BSE": ["bse ltd", "bse limited"], "ANGELONE": ["angel one"], "DELHIVERY": ["delhivery"],
    "IDEA": ["vodafone idea", "vodafone-idea"], "INDIGO": ["indigo", "interglobe aviation"], "DIXON": ["dixon technologies", "dixon"],
    "ADANIGREEN": ["adani green"], "ADANIPOWER": ["adani power"], "ADANIENSOL": ["adani energy solutions", "adani transmission"],
    "AMBUJACEM": ["ambuja cements", "ambuja"], "ACC": ["acc ltd", "acc limited"], "DLF": ["dlf"], "VEDL": ["vedanta"],
    "HINDZINC": ["hindustan zinc"], "MARICO": ["marico"], "DABUR": ["dabur"], "PIDILITIND": ["pidilite"], "HAVELLS": ["havells"],
    "VOLTAS": ["voltas"], "BANKBARODA": ["bank of baroda"], "PNB": ["punjab national bank", "pnb"], "CANBK": ["canara bank"],
    "UNIONBANK": ["union bank of india"], "IDFCFIRSTB": ["idfc first"], "YESBANK": ["yes bank"], "BANDHANBNK": ["bandhan bank"],
    "AUBANK": ["au small finance", "au bank"], "FEDERALBNK": ["federal bank"], "MUTHOOTFIN": ["muthoot finance"],
    "CHOLAFIN": ["cholamandalam"], "LUPIN": ["lupin"], "AUROPHARMA": ["aurobindo pharma"], "DIVISLAB": ["divi's", "divis lab"],
    "TVSMOTOR": ["tvs motor"], "ASHOKLEY": ["ashok leyland"], "ESCORTS": ["escorts kubota"], "BOSCHLTD": ["bosch"],
    "SUZLON": ["suzlon"], "INOXWIND": ["inox wind"], "WAAREEENER": ["waaree energies", "waaree"], "PREMIERENE": ["premier energies"],
    "HYUNDAI": ["hyundai motor india"], "LGEINDIA": ["lg electronics india"], "TATATECH": ["tata technologies"],
})
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


def _tokens(t: str) -> set[str]:
    t = _SUFFIX.sub(" ", t.lower())
    return {w for w in re.sub(r"[^a-z0-9& ]", " ", t).split() if len(w) > 1}


def resolve(db: Session, companies: list[dict]) -> dict[str, str]:
    """Company names / symbols named by the AI -> NSE symbols Syena can trade. A symbol is accepted only if it is in
    the stock list; a name only if it clearly matches one listed company (all its words in that company's name, or
    the same words), never a loose guess."""
    al = instrument_aliases(db)
    out: dict[str, str] = {}
    for c in companies or []:
        sym = str(c.get("symbol") or "").strip().upper()
        name = str(c.get("name") or "").strip()
        if sym and sym in al:
            out[sym] = name or sym
            continue
        nt = _tokens(name)
        if not nt:
            continue
        hits = [s for s, names in al.items() if any(nt == _tokens(n) or (len(nt) >= 2 and nt <= _tokens(n)) for n in names)]
        if len(hits) == 1:
            out[hits[0]] = name
    return out
