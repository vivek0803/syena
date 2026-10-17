from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.api.deps import AuthContext, authenticated
from syena.db import get_db
from syena.models import ModeAudit, User
from syena.services import mode as mode_service

router = APIRouter(prefix="/api/mode", tags=["mode"], dependencies=[Depends(authenticated)])


class ModeOut(BaseModel):
    mode: str
    halted: bool
    halt_reason: str | None
    updated_at: datetime


class GoLiveIn(BaseModel):
    password: str = Field(max_length=256)
    totp: str = Field(max_length=10)
    confirmation: str = Field(max_length=16)


class HaltIn(BaseModel):
    reason: str = Field(default="manual kill switch", max_length=500)


class AuditRow(BaseModel):
    from_mode: str
    to_mode: str
    actor: str
    reason: str
    success: bool
    ip: str | None
    created_at: datetime


def _out(db: Session) -> ModeOut:
    s = mode_service.get_state(db)
    return ModeOut(mode=s.mode.value, halted=s.halted, halt_reason=s.halt_reason, updated_at=s.updated_at)


@router.get("", response_model=ModeOut)
def get_mode(db: Session = Depends(get_db)):
    return _out(db)


@router.post("/live", response_model=ModeOut)
def go_live(body: GoLiveIn, ctx: AuthContext = Depends(authenticated), db: Session = Depends(get_db)):
    user = db.get(User, ctx.user.id)
    try:
        mode_service.request_live(db, user, body.password, body.totp, body.confirmation, ctx.ip)
    except mode_service.ModeSwitchDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.public_message)
    return _out(db)


@router.post("/paper", response_model=ModeOut)
def go_paper(ctx: AuthContext = Depends(authenticated), db: Session = Depends(get_db)):
    mode_service.switch_to_paper(db, ctx.ip)
    return _out(db)


@router.post("/halt", response_model=ModeOut)
def halt(body: HaltIn, ctx: AuthContext = Depends(authenticated), db: Session = Depends(get_db)):
    mode_service.halt(db, body.reason, actor="user", ip=ctx.ip)
    return _out(db)


@router.post("/resume", response_model=ModeOut)
def resume(ctx: AuthContext = Depends(authenticated), db: Session = Depends(get_db)):
    mode_service.resume(db, ctx.ip)
    return _out(db)


@router.get("/audit", response_model=list[AuditRow])
def audit(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(ModeAudit).order_by(ModeAudit.id.desc()).limit(min(limit, 500)))
    return [AuditRow.model_validate(r, from_attributes=True) for r in rows]
