"""Option chain: Black-Scholes IV/Greeks, max pain, OI build-up, the chain from a fake Angel feed, and the API."""
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from syena.market import options as op
from syena.models import OptionContract


def test_iv_round_trip_and_put_call_parity():
    s, k, t = 25000.0, 25200.0, 10 / 365
    for kind in ("CE", "PE"):
        p = op.bs_price(s, k, t, 0.14, kind)
        assert op.implied_vol(p, s, k, t, kind) == pytest.approx(0.14, abs=1e-4)
    c, p = op.bs_price(s, k, t, 0.2, "CE"), op.bs_price(s, k, t, 0.2, "PE")
    assert c - p == pytest.approx(s - k * math.exp(-op.RISK_FREE * t), abs=1e-6)
    g = op.greeks(s, s, t, 0.15, "CE")
    assert 0.5 < g["delta"] < 0.6 and g["theta"] < 0 and g["vega"] > 0
    assert op.greeks(s, s, t, 0.15, "PE")["delta"] == pytest.approx(g["delta"] - 1, abs=1e-3)
    assert op.implied_vol(1.0, s, 24000, t, "CE") is None  # below intrinsic


def test_buildup_and_max_pain():
    assert op.buildup(5, 100) == "long build-up" and op.buildup(-5, 100) == "short build-up"
    assert op.buildup(5, -100) == "short covering" and op.buildup(-5, -100) == "long unwinding" and op.buildup(None, 1) is None
    rows = [{"strike": k, "ce": {"oi": ce}, "pe": {"oi": pe}} for k, ce, pe in
            [(100, 10, 500), (110, 50, 300), (120, 400, 60), (130, 700, 5)]]
    assert op.max_pain(rows) == 120  # payout at 110 = 800, at 120 = 750, at 130 = 1,600


class FakeAngel:
    """Looks like AngelMarketData to the options module."""

    def __init__(self, oi_bump=0):
        self.oi_bump = oi_bump

    def _token_map(self):
        return {}

    def ltp(self, syms):
        return {s: 1000.0 for s in syms}

    def _quote(self, mode, req):
        if "NSE" in req:
            return [{"symbolToken": "99926000", "ltp": 25040.0}]
        out = []
        for tok in req["NFO"]:
            strike, kind = int(tok[:-2]), tok[-2:]
            intrinsic = max(0, 25040 - strike) if kind == "CE" else max(0, strike - 25040)
            px = intrinsic + 60
            out.append({"symbolToken": tok, "ltp": px, "close": px - 5, "opnInterest": 1000 + strike // 10 + self.oi_bump,
                        "tradeVolume": 50, "depth": {"buy": [{"price": px - 0.5}], "sell": [{"price": px + 0.5}]}})
        return out


def seed(db, exp):
    for k in range(24500, 25600, 50):
        for t in ("CE", "PE"):
            db.add(OptionContract(broker="angel", token=f"{k}{t}", tradingsymbol=f"NIFTY{k}{t}", underlying="NIFTY", expiry=exp,
                                  strike=float(k), option_type=t, lot_size=75, kind="index"))
    db.commit()


def test_chain_builds_rows_summary_and_oi_change(db):
    exp = date.today() + timedelta(days=7)
    seed(db, exp)
    now = datetime.now(timezone.utc)
    c = op.chain(db, FakeAngel(), "NIFTY", strikes=5, now=now - timedelta(days=1))
    assert c["atm"] == 25050 and len(c["rows"]) == 11 and c["spot"] == 25040.0 and c["lot_size"] == 75
    r = next(x for x in c["rows"] if x["strike"] == 25050)
    assert r["ce"]["iv"] and r["pe"]["iv"] and r["ce"]["delta"] > 0 > r["pe"]["delta"]
    assert c["summary"]["pcr"] is not None and c["summary"]["straddle"] > 0 and c["summary"]["read"]
    assert r["ce"]["oi_change"] is None and c["oi_change_note"]
    c2 = op.chain(db, FakeAngel(oi_bump=200), "NIFTY", strikes=5, now=now)  # next day: change vs yesterday
    r2 = next(x for x in c2["rows"] if x["strike"] == 25050)
    assert r2["ce"]["oi_change"] == 200 and r2["ce"]["buildup"] == "long build-up" and c2["oi_change_note"] is None
    assert op.underlyings(db)["indices"][0] == "NIFTY"


def test_api_needs_price_source(authed):
    r = authed.get("/api/options/chain")
    assert r.status_code in (409, 502)
