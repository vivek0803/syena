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
    mode_service._live_preconditions.clear()  # tests opt in to preconditions explicitly
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


# ---------------------------------------------------------------- market data + fake LLM helpers
import json as _json  # noqa: E402

import numpy as _np  # noqa: E402
import pandas as _pd  # noqa: E402

from syena.ai import llm as _llm  # noqa: E402
from syena.market.data import FrameMarketData  # noqa: E402


def make_candles(n=400, freq="B", start="2025-01-01", seed=3, drift=0.0006, vol=0.012, price=1500.0):
    rng = _np.random.default_rng(seed)
    if freq == "B":
        idx = _pd.date_range(start, periods=n, freq="B", tz="Asia/Kolkata").tz_convert("UTC") + _pd.Timedelta(hours=10)
    else:
        idx = _pd.date_range(start, periods=n, freq=freq, tz="UTC")
    c = price * _np.exp(_np.cumsum(rng.normal(drift, vol, n)))
    o = c * (1 + rng.normal(0, 0.002, n))
    h = _np.maximum(o, c) * (1 + _np.abs(rng.normal(0, 0.004, n)))
    lo = _np.minimum(o, c) * (1 - _np.abs(rng.normal(0, 0.004, n)))
    return _pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": rng.integers(1e5, 1e6, n).astype(float)}, index=idx)


@pytest.fixture
def md():
    frames = {(s, "day"): make_candles(seed=i + 1) for i, s in enumerate(["INFY", "TCS", "RELIANCE"])}
    frames.update({(s, "15minute"): make_candles(n=800, freq="15min", start="2026-09-01 03:45", seed=i + 11)
                   for i, s in enumerate(["INFY", "TCS", "RELIANCE"])})
    return FrameMarketData(frames, {"NIFTY": {"last": 25000, "prev_close": 24900, "change_pct": 0.4},
                                    "VIX": {"last": 13.5, "prev_close": 14, "change_pct": -3.5}})


class FakeLLM:
    """Routes by prompt type; tests override fields via attributes."""

    def __init__(self):
        self.analysis = {"action": "BUY", "confidence": 82, "sentiment": 0.6, "summary": "Large order win.",
                         "reasoning": "Order win [ID] is material.", "cited": [], "risks": [], "technicals_agree": True}
        self.triage = {"relevant": True, "symbols": ["INFY", "HACKED"], "event_type": "order_win", "reason": "deal"}
        self.review = {"classification": "bad-entry", "decision_quality": "unsound", "right": [], "wrong": [{"point": "x", "evidence": "y"}],
                       "counterfactual": {}, "proposed_lesson": None, "hindsight_flags": [], "summary": "bad entry"}
        self.judge = {"groundedness": 4, "consistency": 5, "no_hallucination": 4, "unsupported_claims": [], "comment": "ok"}
        self.parser = {"status": "clarify", "question": "?", "reason": "ambiguous"}
        self.prompts: list[str] = []

    def __call__(self, model, system, user):
        self.prompts.append(user)
        if system.startswith("You triage"):
            return _json.dumps(self.triage)
        if system.startswith("You are a cautious equity analyst"):
            return "```json\n" + _json.dumps(self.analysis) + "\n```"
        if system.startswith("You are a post-trade reviewer"):
            return _json.dumps(self.review)
        if system.startswith("You grade"):
            return _json.dumps(self.judge)
        if system.startswith("You convert"):
            return _json.dumps(self.parser)
        if system.startswith("You review a proposed manual"):
            return _json.dumps({"verdict": "caution", "issues": [], "suggested": {}, "summary": "ok"})
        return _json.dumps({"text": "pattern"})


@pytest.fixture
def fake_llm():
    f = FakeLLM()
    _llm.set_backend(_llm.FakeBackend(f))
    yield f
    _llm.set_backend(None)


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    # Never call the real API from tests: unavailable unless a test installs the fake backend.
    if _llm._backend is None:
        monkeypatch.setattr(_llm, "available", lambda: _llm._backend is not None)
    yield
