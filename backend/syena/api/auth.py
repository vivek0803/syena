from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.api.deps import AuthContext, authenticated, check_origin, client_ip
from syena.config import get_settings
from syena.db import get_db
from syena.models import LoginAudit
from syena.security.sessions import COOKIE_NAME, create_session, revoke
from syena.services import auth as auth_service

public_router = APIRouter(prefix="/api/auth", tags=["auth"])
router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(authenticated)])


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    totp: str = Field(max_length=10)


class LoginOut(BaseModel):
    username: str
    csrf_token: str


class MeOut(BaseModel):
    username: str
    csrf_token: str
    session_started: datetime


class LoginRow(BaseModel):
    username_attempted: str
    success: bool
    ip: str | None
    user_agent: str | None
    created_at: datetime


@public_router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    ip = client_ip(request)
    ua = request.headers.get("user-agent")
    try:
        user = auth_service.login(db, body.username, body.password, body.totp, ip, ua)
    except auth_service.LoginFailed:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, auth_service.GENERIC_LOGIN_ERROR)
    new = create_session(db, user, ip, ua)
    db.commit()
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        new.cookie_value,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path="/",
        max_age=s.session_absolute_hours * 3600,
    )
    return LoginOut(username=user.username, csrf_token=new.csrf_token)


@router.get("/me", response_model=MeOut)
def me(ctx: AuthContext = Depends(authenticated)):
    return MeOut(username=ctx.user.username, csrf_token=ctx.session.csrf_token, session_started=ctx.session.created_at)


@router.post("/logout", status_code=204)
def logout(response: Response, ctx: AuthContext = Depends(authenticated), db: Session = Depends(get_db)):
    revoke(db, ctx.session)
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@router.get("/logins", response_model=list[LoginRow])
def login_history(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(LoginAudit).order_by(LoginAudit.id.desc()).limit(min(limit, 500)))
    return [LoginRow.model_validate(r, from_attributes=True) for r in rows]
