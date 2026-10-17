from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena.api.deps import authenticated
from syena.db import get_db
from syena.market import kite as kite_session
from syena.models import MFSchemeHolding
from syena.portfolio import analysis, disclosures, sources

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"], dependencies=[Depends(authenticated)])
MAX_UPLOAD = 25_000_000


@router.get("")
def get_portfolio(db: Session = Depends(get_db)):
    return analysis.analyse(db)


@router.post("/sync/zerodha")
def sync_zerodha(db: Session = Depends(get_db)):
    try:
        snap = sources.sync_zerodha(db)
    except kite_session.KiteNotConnected as e:
        raise HTTPException(409, str(e))
    return {"snapshot_id": snap.id}


class AngelLogin(BaseModel):
    client_code: str = Field(max_length=32)
    pin: str = Field(max_length=16)
    totp: str = Field(max_length=10)


@router.post("/angel/login")
def angel_login(body: AngelLogin, db: Session = Depends(get_db)):
    try:
        sources.angel_login(db, body.client_code, body.pin, body.totp)
        snap = sources.sync_angel(db)
    except sources.SourceError as e:
        raise HTTPException(400, str(e))
    return {"snapshot_id": snap.id, **sources.angel_status(db)}


@router.post("/sync/angel")
def sync_angel(db: Session = Depends(get_db)):
    try:
        return {"snapshot_id": sources.sync_angel(db).id}
    except sources.SourceError as e:
        raise HTTPException(409, str(e))


@router.get("/angel/status")
def angel_status(db: Session = Depends(get_db)):
    return sources.angel_status(db)


async def _read(f: UploadFile) -> bytes:
    data = await f.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file too large")
    return data


@router.post("/cas")
async def upload_cas(file: UploadFile = File(...), password: str = Form(...), db: Session = Depends(get_db)):
    data = await _read(file)
    try:
        snap = sources.import_cas(db, data, password)
    except sources.SourceError as e:
        raise HTTPException(400, str(e))
    return {"snapshot_id": snap.id, "note": snap.note}


@router.post("/disclosure")
async def upload_disclosure(file: UploadFile = File(...), as_of: date = Form(...), mapping: str = Form(default="{}"),
                            db: Session = Depends(get_db)):
    import json

    data = await _read(file)
    try:
        return disclosures.import_disclosure(db, data, as_of, json.loads(mapping or "{}"))
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, str(e))


class DisclosureUrl(BaseModel):
    url: str = Field(max_length=1000)
    as_of: date
    mapping: dict[str, str] = Field(default_factory=dict)


@router.post("/disclosure-url")
def disclosure_url(body: DisclosureUrl, db: Session = Depends(get_db)):
    try:
        return disclosures.import_disclosure(db, disclosures.fetch_url(body.url), body.as_of, body.mapping)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/schemes")
def schemes(db: Session = Depends(get_db)):
    out = []
    for h in disclosures.user_schemes(db):
        as_of = db.scalar(select(func.max(MFSchemeHolding.as_of)).where(MFSchemeHolding.scheme_key == (h.isin or h.name)))
        out.append({"name": h.name, "isin": h.isin, "amc": h.amc, "value": h.value,
                    "disclosure_as_of": as_of.isoformat() if as_of else None})
    return out
