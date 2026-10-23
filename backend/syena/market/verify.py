"""Check Syena's daily candles against NSE's official end-of-day file (the "bhavcopy").

NSE publishes every share's official open/high/low/close/volume each evening at
nsearchives.nseindia.com. It is the source everyone else's charts come from, and it is independent of the broker
that supplies Syena's prices, so if both agree the chart is right. NSE's "close price" is the average of the last
30 minutes of trading, while brokers often show the last traded price as the close, so a close is accepted when it
matches either of the two.
"""
import csv
import io
import logging
import threading
import zipfile
from datetime import date, datetime, timedelta

import httpx

from syena.news.sources import UA

log = logging.getLogger("syena.verify")

URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
SERIES = {"EQ", "BE", "BZ", "SM", "ST"}
PRICE_TOL = 0.003   # 0.3%
VOLUME_TOL = 0.03   # 3%: brokers sometimes leave out a late block trade

_cache: dict[date, dict[str, dict] | None] = {}
_lock = threading.Lock()


# Column names in NSE's current (UDiFF) file, with the older bhavcopy's names as a fallback.
COLUMNS = {"symbol": ("TckrSymb", "SYMBOL"), "series": ("SctySrs", "SERIES"), "open": ("OpnPric", "OPEN"),
           "high": ("HghPric", "HIGH"), "low": ("LwPric", "LOW"), "close": ("ClsPric", "CLOSE"), "last": ("LastPric", "LAST"),
           "prev_close": ("PrvsClsgPric", "PREVCLOSE"), "volume": ("TtlTradgVol", "TOTTRDQTY")}


def parse(data: bytes) -> dict[str, dict]:
    """{symbol: {open, high, low, close, last, prev_close, volume}} from the zipped bhavcopy CSV."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    col = {}
    for key, names in COLUMNS.items():
        hit = next((n for n in names if n in header), None)
        if hit is None:
            raise ValueError(f"NSE's file format changed: no {key} column (columns: {', '.join(header[:12])}...)")
        col[key] = hit
    out = {}
    for r in reader:
        if (r.get(col["series"]) or "").strip() not in SERIES:
            continue
        try:
            out[r[col["symbol"]].strip()] = {k: float(r[col[k]]) for k in ("open", "high", "low", "close", "last", "prev_close", "volume")}
        except (KeyError, ValueError, TypeError):
            continue
    return out


def bhavcopy(d: date, get=None) -> dict[str, dict] | None:
    """The day's official prices, or None when NSE has no file (holiday, weekend, not published yet)."""
    with _lock:
        if d in _cache:
            return _cache[d]
    get = get or (lambda u: httpx.get(u, headers={"User-Agent": UA, "Accept": "*/*", "Referer": "https://www.nseindia.com/"},
                                      timeout=20, follow_redirects=True))
    r = get(URL.format(d=d))
    if r.status_code == 404:
        result = None
    else:
        r.raise_for_status()
        result = parse(r.content)
    if result is not None or d < date.today():
        with _lock:
            _cache[d] = result
            while len(_cache) > 30:
                _cache.pop(min(_cache))
    return result


def _diff(ours: float, theirs: float) -> float:
    return abs(ours / theirs - 1) if theirs else (0.0 if not ours else 1.0)


def compare_day(ours: dict, nse: dict) -> list[str]:
    """What doesn't match on one day (empty list = all good)."""
    bad = []
    for k in ("open", "high", "low"):
        if _diff(ours[k], nse[k]) > PRICE_TOL:
            bad.append(f"{k} {ours[k]:.2f} vs NSE {nse[k]:.2f}")
    if min(_diff(ours["close"], nse["close"]), _diff(ours["close"], nse["last"])) > PRICE_TOL:
        bad.append(f"close {ours['close']:.2f} vs NSE close {nse['close']:.2f} / last trade {nse['last']:.2f}")
    if nse["volume"] and _diff(ours["volume"], nse["volume"]) > VOLUME_TOL:
        bad.append(f"volume {ours['volume']:,.0f} vs NSE {nse['volume']:,.0f}")
    return bad


def verify(symbol: str, daily, days: int = 5, today: date | None = None, get=None) -> dict:
    """Compare the last `days` completed trading days of `daily` (Syena's candles) with NSE's files."""
    import pandas as pd

    today = today or (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    ours = {}
    if daily is not None and len(daily):
        idx = daily.index.tz_convert("Asia/Kolkata") if daily.index.tz is not None else daily.index
        for ts, r in zip(idx, daily.itertuples()):
            ours[ts.date()] = {"open": float(r.open), "high": float(r.high), "low": float(r.low),
                               "close": float(r.close), "volume": float(r.volume)}
    rows, d, tries = [], today, 0
    while len(rows) < days and tries < days + 12:
        tries += 1
        try:
            nse_day = bhavcopy(d, get=get)
        except Exception as e:  # network trouble: report rather than fail the page
            return {"symbol": symbol, "ok": None, "error": f"Couldn't download NSE's file for {d}: {type(e).__name__}", "days": rows}
        if nse_day is not None:
            nse = nse_day.get(symbol)
            mine = ours.get(d)
            if nse is None:
                rows.append({"date": str(d), "status": "not_on_nse", "detail": "not traded on NSE that day"})
            elif mine is None:
                rows.append({"date": str(d), "status": "missing", "detail": "Syena has no candle for this day", "nse": nse})
            else:
                bad = compare_day(mine, nse)
                rows.append({"date": str(d), "status": "match" if not bad else "mismatch", "detail": "; ".join(bad),
                             "ours": mine, "nse": nse})
        d -= timedelta(days=1)
    checked = [r for r in rows if r["status"] in ("match", "mismatch", "missing")]
    good = sum(r["status"] == "match" for r in checked)
    return {"symbol": symbol, "ok": bool(checked) and good == len(checked), "matched": good, "checked": len(checked),
            "days": rows, "source": "NSE bhavcopy (official end-of-day prices)", "tolerance_pct": PRICE_TOL * 100}
