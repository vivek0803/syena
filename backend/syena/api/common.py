"""Shared API helpers: serialisation, market-data dependency, error mapping."""
import enum
from datetime import date, datetime

from fastapi import Depends, HTTPException
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from syena.db import get_db
from syena.market import kite as kite_session
from syena.market.data import KiteMarketData


def ser(obj, exclude: tuple = (), extra: dict | None = None) -> dict:
    if obj is None:
        return None
    out = {}
    for c in inspect(obj).mapper.column_attrs:
        if c.key in exclude:
            continue
        v = getattr(obj, c.key)
        if isinstance(v, enum.Enum):
            v = v.value
        elif isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[c.key] = v
    if extra:
        out.update(extra)
    return out


def market_data(db: Session = Depends(get_db)) -> KiteMarketData:
    if kite_session.active_token(db) is None:
        raise HTTPException(409, "Connect Zerodha first: market data comes from your Kite session.")
    return KiteMarketData(db)


def bad_request(e: Exception, code: int = 400):
    raise HTTPException(code, str(e))
