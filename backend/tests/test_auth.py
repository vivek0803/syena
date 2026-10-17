from datetime import timedelta

from sqlalchemy import select

from syena.db import utcnow
from syena.main import PUBLIC_PATHS, VERIFIED_PATHS
from syena.models import LoginAudit, User, UserSession
from syena.security.sessions import COOKIE_NAME
from tests.conftest import ORIGIN, PASSWORD, do_login


def test_every_non_public_route_requires_auth(client):
    # Enumerate via the OpenAPI schema so nested/included routers are all covered.
    checked = 0
    for path, ops in client.app.openapi()["paths"].items():
        if path in PUBLIC_PATHS or path in VERIFIED_PATHS:
            continue
        for method in ops:
            r = client.request(method.upper(), path, headers=ORIGIN, json={})
            assert r.status_code == 401, f"{method} {path} returned {r.status_code}"
            checked += 1
    assert checked >= 60


def test_verified_public_paths_reject_without_proof(client):
    r = client.get("/kite/callback?request_token=x&status=success&nonce=forged", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?kite=invalid"
    r = client.post("/api/telegram/webhook", json={"message": {"chat": {"id": 1}, "text": "/halt"}})
    assert r.status_code == 403


def test_login_success_sets_hardened_cookie(client, clock):
    r = do_login(client, clock)
    assert r.status_code == 200
    assert r.json()["csrf_token"]
    cookie = r.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/auth/me").status_code == 200


def test_failed_logins_are_indistinguishable(client, clock):
    bodies = {
        do_login(client, clock, password="wrong password!!").text,
        do_login(client, clock, code="000000").text,
        do_login(client, clock, username="nobody").text,
    }
    assert bodies == {'{"detail":"Invalid credentials"}'}


def test_totp_code_cannot_be_replayed(client, clock):
    code = clock.fresh_code()
    assert do_login(client, clock, code=code).status_code == 200
    assert do_login(client, clock, code=code).status_code == 401


def test_lockout_after_repeated_failures(client, clock, db):
    for _ in range(5):
        assert do_login(client, clock, password="wrong password!!").status_code == 401
    # Correct credentials still refused while locked
    assert do_login(client, clock).status_code == 401
    user = db.scalar(select(User))
    db.refresh(user)
    assert user.locked_until is not None
    # Lock expires
    user.locked_until = utcnow() - timedelta(seconds=1)
    db.commit()
    assert do_login(client, clock).status_code == 200


def test_ip_rate_limit(client, clock):
    statuses = [do_login(client, clock, username=f"x{i}").status_code for i in range(12)]
    assert statuses.count(401) == 12  # all generic failures...
    # ...and the 11th+ are rate-limited even with correct credentials
    assert do_login(client, clock).status_code == 401


def test_logins_are_audited_with_ip(client, clock, db):
    do_login(client, clock, password="wrong password!!")
    do_login(client, clock)
    rows = db.scalars(select(LoginAudit).order_by(LoginAudit.id)).all()
    assert [r.success for r in rows] == [False, True]
    assert rows[0].reason == "bad_password" and rows[0].ip


def test_csrf_required_on_state_change(authed):
    token = authed.headers.pop("X-CSRF-Token")
    assert authed.post("/api/mode/paper").status_code == 403
    assert authed.post("/api/mode/paper", headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert authed.post("/api/mode/paper", headers={"X-CSRF-Token": token}).status_code == 200


def test_cross_origin_rejected(authed, client, clock):
    assert authed.post("/api/mode/paper", headers={"Origin": "https://evil.example"}).status_code == 403
    assert do_login(client, clock, headers={"Origin": "https://evil.example"}).status_code == 403


def test_idle_timeout(authed, db):
    s = db.scalar(select(UserSession))
    s.last_seen_at = utcnow() - timedelta(minutes=31)
    db.commit()
    assert authed.get("/api/auth/me").status_code == 401


def test_logout_revokes_session(authed):
    assert authed.post("/api/auth/logout").status_code == 204
    assert authed.get("/api/auth/me").status_code == 401


def test_security_headers(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_password_hash_is_argon2id(db, user):
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


def test_login_history_endpoint(authed):
    rows = authed.get("/api/auth/logins").json()
    assert rows and rows[0]["success"] is True and "reason" not in rows[0]
