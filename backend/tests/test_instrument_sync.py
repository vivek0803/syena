import pytest

from syena.market import data


class FakeKite:
    def instruments(self, exchange):
        return [
            dict(instrument_token=1, tradingsymbol="TCS", name="TCS", segment="NSE", instrument_type="EQ", tick_size=0.05, lot_size=1),
            dict(instrument_token=2, tradingsymbol="NIFTY 50", name="", segment="INDICES", instrument_type="EQ"),
        ]


def test_sync_is_idempotent_and_lazy_lookup(db):
    md = data.KiteMarketData(db)
    md._kite = lambda: FakeKite()
    assert md.sync_instruments() == 1
    assert md.sync_instruments() == 1  # re-sync replaces, no duplicate-key error
    assert md._token("TCS") == 1
    with pytest.raises(ValueError, match="not an NSE equity symbol"):
        md._token("NOPE")


def test_lookup_syncs_when_list_is_empty(db):
    md = data.KiteMarketData(db)
    md._kite = lambda: FakeKite()
    assert md._token("TCS") == 1
