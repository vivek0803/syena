"""Angel One SmartAPI market data with a fake HTTP layer (no network)."""
from datetime import datetime, timedelta, timezone

import pyotp
import pytest

from syena.config import get_settings
from syena.market import angel, source


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        if self.body is ValueError:  # Angel's rate-limit page is not JSON
            raise ValueError("not json")
        return self.body

    def raise_for_status(self):
        pass


class FakeHTTP:
    def __init__(self):
        self.calls = []
        self.logins = 0
        self.expire_next = False

    def get(self, url, timeout=None):
        self.calls.append(("GET", url))
        return Resp([{"symbol": "TCS-EQ", "exch_seg": "NSE", "token": "11536"},
                     {"symbol": "TCS", "exch_seg": "BSE", "token": "532540"},
                     {"symbol": "RELIANCE-EQ", "exch_seg": "NSE", "token": 2885}])

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        if url.endswith("loginByPassword"):
            self.logins += 1
            assert json["totp"].isdigit() and len(json["totp"]) == 6
            return Resp({"status": True, "data": {"jwtToken": f"jwt{self.logins}"}})
        assert headers["Authorization"].startswith("Bearer jwt")
        if self.expire_next:
            self.expire_next = False
            return Resp({"status": False, "message": "Invalid Token", "errorcode": "AG8001"}, 401)
        if url.endswith("/quote/"):
            toks = next(iter(json["exchangeTokens"].values()))
            return Resp({"status": True, "data": {"fetched": [
                {"symbolToken": t, "ltp": 100.0 + int(t) % 7, "close": 99.0} for t in toks]}})
        if url.endswith("getCandleData"):
            return Resp({"status": True, "data": [["2026-10-15T00:00:00+05:30", 1, 2, 0.5, 1.5, 1000],
                                                  ["2026-10-16T00:00:00+05:30", 1.5, 2.5, 1, 2, 1200]]})
        raise AssertionError(url)


@pytest.fixture
def angel_env(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "angel_api_key", "k")
    monkeypatch.setattr(s, "angel_client_id", "A123")
    monkeypatch.setattr(s, "angel_pin", type(s.angel_pin)("1234"))
    monkeypatch.setattr(s, "angel_totp_secret", type(s.angel_totp_secret)(pyotp.random_base32()))
    monkeypatch.setattr(s, "market_data", "angel")
    return s


def test_not_configured_gives_clear_message(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "market_data", "angel")
    assert not source.ready(db)
    with pytest.raises(Exception, match="SYENA_ANGEL_CLIENT_ID"):
        source.get_market_data(db)


def test_ltp_candles_and_indices(db, angel_env):
    http = FakeHTTP()
    md = angel.AngelMarketData(db, http=http)
    assert source.ready(db)
    assert md.ltp(["TCS", "RELIANCE", "NOPE"]) == {"TCS": 100.0 + 11536 % 7, "RELIANCE": 100.0 + 2885 % 7}
    assert http.logins == 1
    df = md.candles("TCS", "day", datetime(2026, 10, 14, tzinfo=timezone.utc), datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert list(df["close"]) == [1.5, 2.0]
    snap = md.index_snapshot()
    assert set(snap) == {"NIFTY", "VIX", "SENSEX"}
    with pytest.raises(ValueError, match="not an NSE equity symbol"):
        md.token("NOPE")
    # session reused, not re-logged in
    assert http.logins == 1


def test_relogin_once_on_expired_token(db, angel_env):
    http = FakeHTTP()
    md = angel.AngelMarketData(db, http=http)
    md.ltp(["TCS"])
    http.expire_next = True
    assert md.ltp(["TCS"])["TCS"] > 0
    assert http.logins == 2


def test_bad_totp_secret(db, angel_env, monkeypatch):
    monkeypatch.setattr(angel_env, "angel_totp_secret", type(angel_env.angel_totp_secret)("not base32 !!"))
    with pytest.raises(angel.AngelError, match="TOTP"):
        angel.login(db, http=FakeHTTP())


def test_rate_limit_waits_instead_of_logging_in_again(db, angel_env, monkeypatch):
    slept = []
    monkeypatch.setattr(angel, "_sleep", slept.append)
    http = FakeHTTP()
    md = angel.AngelMarketData(db, http=http)
    md.ltp(["TCS"])
    real = http.post
    limited = {"n": 2}
    def post(url, headers=None, json=None, timeout=None):
        if url.endswith("/quote/") and limited["n"]:
            limited["n"] -= 1
            http.calls.append(("POST", url, json))
            return Resp({"status": False, "message": "Access denied because of exceeding access rate"}, 403)
        return real(url, headers=headers, json=json, timeout=timeout)
    http.post = post
    assert md.ltp(["TCS"])["TCS"] > 0
    assert http.logins == 1 and slept == [1.5, 3.0]  # backed off twice, same session
    limited["n"] = 10
    with pytest.raises(angel.AngelError, match="too many requests"):
        md.ltp(["TCS"])
    assert http.logins == 1


def test_refused_login_pauses_further_logins(db, angel_env):
    class Refusing(FakeHTTP):
        def post(self, url, headers=None, json=None, timeout=None):
            if url.endswith("loginByPassword"):
                self.logins += 1
                return Resp(ValueError, 403)
            return super().post(url, headers, json, timeout)
    http = Refusing()
    with pytest.raises(angel.AngelError, match="limiting requests"):
        angel.login(db, http=http)
    with pytest.raises(angel.AngelError, match="limiting requests"):
        angel.login(db, http=http)
    assert http.logins == 1  # the second attempt never reached Angel
