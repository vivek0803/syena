"""Picks where prices come from: Angel One SmartAPI (free) or Zerodha Kite (paid Kite Connect app).

Set SYENA_MARKET_DATA=angel or kite. Orders always go through Zerodha; this only affects prices/candles.
"""
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.market import kite as kite_session


def provider() -> str:
    return "angel" if get_settings().market_data.strip().lower() == "angel" else "kite"


def ready(db: Session) -> bool:
    if provider() == "angel":
        from syena.market import angel

        return angel.configured()
    return kite_session.active_token(db) is not None


def get_market_data(db: Session):
    if provider() == "angel":
        from syena.market import angel

        if not angel.configured():
            raise kite_session.KiteNotConnected(
                "Angel One price data is not set up: fill in " + ", ".join(angel.missing_settings())
                + " in deploy/syena-local.conf, then run update-syena.bat")
        return angel.AngelMarketData(db)
    from syena.market.data import KiteMarketData

    if kite_session.active_token(db) is None:
        raise kite_session.KiteNotConnected("Connect Zerodha first: market data comes from your Kite session.")
    return KiteMarketData(db)
