import ast
import pathlib

import pyotp
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from syena.config import Settings
from syena.models import ModeAudit, TradingMode
from syena.services import mode as mode_service
from tests.conftest import PASSWORD, TOTP_SECRET

SRC = pathlib.Path(__file__).resolve().parents[1] / "syena"


def go_live(authed, clock, password=PASSWORD, code=None, confirmation="LIVE"):
    return authed.post(
        "/api/mode/live",
        json={"password": password, "totp": code or clock.fresh_code(), "confirmation": confirmation},
    )


def test_default_mode_is_paper(authed):
    assert authed.get("/api/mode").json()["mode"] == "PAPER"


def test_go_live_with_all_three_factors(authed, clock, db):
    r = go_live(authed, clock)
    assert r.status_code == 200 and r.json()["mode"] == "LIVE"
    audit = db.scalars(select(ModeAudit)).all()[-1]
    assert (audit.to_mode, audit.actor, audit.success) == ("LIVE", "user", True) and audit.ip


def test_live_denied_without_exact_confirmation(authed, clock):
    for phrase in ["live", "LIVE ", "", "Live", "YES"]:
        r = go_live(authed, clock, confirmation=phrase)
        assert r.status_code == 403
    assert authed.get("/api/mode").json()["mode"] == "PAPER"


def test_live_denied_with_bad_password_or_totp(authed, clock, db):
    assert go_live(authed, clock, password="nope nope nope").status_code == 403
    assert go_live(authed, clock, code="123456").status_code == 403
    denied = db.scalars(select(ModeAudit).where(ModeAudit.success.is_(False))).all()
    assert len(denied) == 2
    assert authed.get("/api/mode").json()["mode"] == "PAPER"


def test_live_totp_cannot_reuse_login_code(authed, clock):
    reused = pyotp.TOTP(TOTP_SECRET).at(clock.t)  # code consumed by the login
    assert go_live(authed, clock, code=reused).status_code == 403


def test_halt_drops_to_paper_and_blocks_live(authed, clock):
    go_live(authed, clock)
    r = authed.post("/api/mode/halt", json={"reason": "test kill"})
    assert r.json() == {**r.json(), "mode": "PAPER", "halted": True}
    assert go_live(authed, clock).status_code == 403
    r = authed.post("/api/mode/resume")
    assert r.json()["mode"] == "PAPER" and r.json()["halted"] is False
    assert go_live(authed, clock).status_code == 200


def test_live_precondition_hook(authed, clock):
    mode_service.register_live_precondition("kite_token", lambda db: "Kite token expired")
    r = go_live(authed, clock)
    assert r.status_code == 403 and r.json()["detail"] == "Kite token expired"


def test_switch_to_paper_is_one_click(authed, clock):
    go_live(authed, clock)
    assert authed.post("/api/mode/paper").json()["mode"] == "PAPER"


def test_restart_forces_paper(db, user):
    state = mode_service.get_state(db)
    state.mode = TradingMode.LIVE
    db.commit()
    mode_service.on_startup(db)
    assert mode_service.current_mode(db) == TradingMode.PAPER
    last = db.scalars(select(ModeAudit)).all()[-1]
    assert last.actor == "system" and "restart" in last.reason


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    use_good_password=st.booleans(),
    use_good_totp=st.booleans(),
    confirmation=st.sampled_from(["LIVE", "live", "LIVE ", "", "L1VE", "yes"]),
    halted=st.booleans(),
)
def test_property_no_live_without_all_factors(db, user, clock, use_good_password, use_good_totp, confirmation, halted):
    state = mode_service.get_state(db)
    state.mode, state.halted, state.halt_reason = TradingMode.PAPER, halted, "x" if halted else None
    user.failed_logins, user.locked_until = 0, None
    db.commit()
    pw = PASSWORD if use_good_password else "definitely wrong pw"
    code = clock.fresh_code() if use_good_totp else "000000"
    try:
        mode_service.request_live(db, user, pw, code, confirmation, ip="1.2.3.4")
    except mode_service.ModeSwitchDenied:
        pass
    all_ok = use_good_password and use_good_totp and confirmation == "LIVE" and not halted
    assert (mode_service.current_mode(db) == TradingMode.LIVE) == all_ok


def test_no_config_or_cli_path_to_mode():
    assert not [f for f in Settings.model_fields if "mode" in f.lower()]
    cli = (SRC / "cli.py").read_text()
    for forbidden in ("services.mode", "services import mode", "SystemState", "TradingMode", "request_live"):
        assert forbidden not in cli
    # Only the authenticated API route may call request_live.
    callers = [p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if "request_live(" in p.read_text()]
    assert sorted(callers) == ["api/mode.py", "services/mode.py"]


def test_mode_is_only_assigned_in_mode_service():
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "mode":
                    offenders.append(str(path.relative_to(SRC)))
    assert set(offenders) <= {"services/mode.py"}, offenders
