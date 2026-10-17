import os

from cryptography.fernet import Fernet

# Must be set before syena modules read settings.
os.environ.update(
    {
        "SYENA_ENV": "test",
        "SYENA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "SYENA_PUBLIC_ORIGIN": "http://testserver",
        "SYENA_COOKIE_SECURE": "false",
        "SYENA_LOG_TO_DB": "false",
        "SYENA_DATABASE_URL": "sqlite:///:memory:",
    }
)

import pyotp  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from syena import db as dbmod  # noqa: E402
from syena.db import Base, make_engine  # noqa: E402
from syena.models import User  # noqa: E402
from syena.security import totp as totp_mod  # noqa: E402
from syena.security.passwords import hash_password  # noqa: E402
from syena.services import auth as auth_service  # noqa: E402
from syena.services import mode as mode_service  # noqa: E402

USERNAME = "vivek"
PASSWORD = "correct horse battery staple"
TOTP_SECRET = pyotp.random_base32()
ORIGIN = {"Origin": "http://testserver"}


class FakeClock:
    """Controls time for TOTP so each test login can use a fresh, valid time step."""

    def __init__(self):
        self.t = 1_800_000_000.0

    def time(self) -> float:
        return self.t

    def fresh_code(self) -> str:
        self.t += 30
        return pyotp.TOTP(TOTP_SECRET).at(self.t)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(totp_mod.time, "time", c.time)
    return c


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(eng)
    dbmod.set_engine(eng)
    auth_service.ip_limiter.reset()
    saved = list(mode_service._live_preconditions)
    yield eng
    mode_service._live_preconditions[:] = saved
    eng.dispose()


@pytest.fixture
def db(engine):
    s = dbmod.SessionLocal()
    yield s
    s.close()


@pytest.fixture
def user(db):
    u = User(username=USERNAME, password_hash=hash_password(PASSWORD), totp_secret_enc=totp_mod.encrypt_secret(TOTP_SECRET))
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def client(engine, user):
    from syena.main import create_app

    app = create_app(run_startup=False)
    with TestClient(app) as c:
        yield c


def do_login(client, clock, password=PASSWORD, code=None, username=USERNAME, headers=ORIGIN):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password, "totp": code or clock.fresh_code()},
        headers=headers,
    )


@pytest.fixture
def authed(client, clock):
    r = do_login(client, clock)
    assert r.status_code == 200, r.text
    client.headers.update({"X-CSRF-Token": r.json()["csrf_token"], **ORIGIN})
    return client
