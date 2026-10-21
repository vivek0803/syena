"""Sector classification: NSE's sectors where NSE publishes them, other labels translated to NSE names."""
from syena.models import AppSetting, Stock
from syena.screener import data, sectors


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status = text, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(self.status)


TOTAL = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
         "Reliance,Oil Gas & Consumable Fuels,RELIANCE,EQ,X\nL&T,Construction,LT,EQ,Y\n")
MICRO = "Company Name,Industry,Symbol,Series,ISIN Code\nTiny,Capital Goods,TINY,EQ,Z\nL&T,Capital Goods,LT,EQ,Y\n"


def test_canonical_names():
    assert sectors.canonical("Technology", "Information Technology Services") == "Information Technology"
    assert sectors.canonical("Industrials", "Electrical Equipment & Parts") == "Capital Goods"  # not Power
    assert sectors.canonical("Utilities", "Utilities - Regulated Electric") == "Power"
    assert sectors.canonical("Consumer Defensive", "Packaged Foods") == "Fast Moving Consumer Goods"
    assert sectors.canonical("Basic Materials", "Building Materials") == "Construction Materials"
    assert sectors.canonical("Financial Services", "Banks - Regional") == "Financial Services"
    assert sectors.canonical("Consumer Cyclical", None) == "Consumer Durables"
    assert sectors.canonical("Capital Goods", "whatever") == "Capital Goods"  # already NSE: unchanged
    assert sectors.canonical("Pharmaceuticals", None) == "Healthcare"  # Screener.in-style label
    assert sectors.canonical(None, None) is None


def test_sync_applies_nse_and_translates_the_rest(db):
    for sym, sec, ind in [("RELIANCE", "Energy", "Oil & Gas Refining & Marketing"), ("LT", "Industrials", "Engineering & Construction"),
                          ("TINY", None, None), ("SOFTCO", "Technology", "Software - Application"), ("NOSEC", None, None)]:
        db.add(Stock(symbol=sym, active=True, sector=sec, industry=ind))
    db.commit()
    def get(url):
        if "totalmarket" in url:
            return Resp(TOTAL)
        if "microcap" in url:
            return Resp(MICRO)
        return Resp("", 404)
    out = sectors.sync(db, get)
    assert out["nse_classified"] == 3 and out["downloaded"]
    got = {s.symbol: s.sector for s in db.query(Stock)}
    assert got == {"RELIANCE": "Oil Gas & Consumable Fuels", "LT": "Construction",  # first list wins
                   "TINY": "Capital Goods", "SOFTCO": "Information Technology", "NOSEC": None}
    # Yahoo's later fundamentals keep NSE's sector for NSE-classified stocks, translate for others
    data.apply_fundamentals(db.get(Stock, "LT"), {"sector": "Industrials", "industry": "Conglomerates"}, "yahoo")
    data.apply_fundamentals(db.get(Stock, "NOSEC"), {"sector": "Consumer Defensive", "industry": "Tobacco"}, "yahoo")
    assert db.get(Stock, "LT").sector == "Construction" and db.get(Stock, "NOSEC").sector == "Fast Moving Consumer Goods"
    # NSE unreachable next week: the saved classification is kept
    row = db.get(AppSetting, sectors.KEY)
    row.value = {**row.value, "at": "2020-01-01T00:00:00+00:00"}
    db.commit()
    assert sectors.due(db)
    assert sectors.sync(db, lambda u: Resp("", 500))["nse_classified"] == 3
