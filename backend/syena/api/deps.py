"""Request dependencies: client IP, authentication, CSRF + Origin checks.

`authenticated` is attached at router level to every protected router, so new
routes are protected by default. tests/test_auth.py enumerates all routes and
asserts that everything except the public allowlist returns 401 without a session.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from syena.config import get_settings
from syena.db import get_db
from syena.models import User, UserSession
from syena.security.sessions import COOKIE_NAME, CSRF_HEADER, csrf_matches, load_session

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def client_ip(request: Request) -> str | None:
    if get_settings().trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[-1].strip()[:64]
    return request.client.host if request.client else None


def origin_allowed(request: Request) -> bool:
    """For state-changing requests the Origin (or Referer) must be our own public origin."""
    allowed = get_settings().public_origin.rstrip("/")
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/") == allowed
    referer = request.headers.get("referer")
    if referer:
        return referer.startswith(allowed + "/") or referer.rstrip("/") == allowed
    return False


def check_origin(request: Request) -> None:
    if request.method in UNSAFE_METHODS and not origin_allowed(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-origin request rejected")


@dataclass
class AuthContext:
    user: User
    session: UserSession
    ip: str | None


def authenticated(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    row = load_session(db, request.cookies.get(COOKIE_NAME))
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if request.method in UNSAFE_METHODS:
        check_origin(request)
        if not csrf_matches(row, request.headers.get(CSRF_HEADER)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return AuthContext(user=user, session=row, ip=client_ip(request))
