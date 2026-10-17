"""Trading mode control.

Rules (spec section 14):
- Mode lives only in the DB (system_state.mode). Default PAPER.
- The ONLY path to LIVE is request_live(), which requires an authenticated user,
  password re-entry, a fresh TOTP code, and the literal confirmation "LIVE".
  It is called only by the authenticated API route; there is no env/CLI/Telegram path.
- Fallback to PAPER is always allowed (user one-click, or system on restart,
  token expiry, daily-loss halt, repeated broker errors).
- Every change and every denied LIVE attempt is written to mode_audit_log.
"""
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.models import ModeAudit, SystemState, TradingMode, User
from syena.services.auth import verify_credentials

log = logging.getLogger("syena.mode")

LIVE_CONFIRMATION_PHRASE = "LIVE"

# Extra conditions that must hold before going LIVE. Each returns an error string or None.
# Phase 2 registers the Kite-token check; later phases add more.
LivePrecondition = Callable[[Session], "str | None"]
_live_preconditions: list[tuple[str, LivePrecondition]] = []


def register_live_precondition(name: str, check: LivePrecondition) -> None:
    _live_preconditions.append((name, check))


class ModeSwitchDenied(Exception):
    def __init__(self, reason: str, public_message: str):
        super().__init__(reason)
        self.reason = reason
        self.public_message = public_message


def get_state(db: Session) -> SystemState:
    state = db.get(SystemState, 1)
    if state is None:
        state = SystemState(id=1, mode=TradingMode.PAPER, halted=False)
        db.add(state)
        db.commit()
    return state


def current_mode(db: Session) -> TradingMode:
    return get_state(db).mode


def _audit(db: Session, frm: TradingMode, to: TradingMode, actor: str, reason: str, success: bool, ip: str | None):
    db.add(ModeAudit(from_mode=frm.value, to_mode=to.value, actor=actor, reason=reason, success=success, ip=ip))


def _set(db: Session, state: SystemState, to: TradingMode, actor: str, reason: str, ip: str | None) -> None:
    frm = state.mode
    state.mode = to
    state.updated_at = utcnow()
    _audit(db, frm, to, actor, reason, True, ip)
    db.commit()
    log.warning("mode changed", extra={"from": frm.value, "to": to.value, "actor": actor, "reason": reason})


def fallback_to_paper(db: Session, reason: str, ip: str | None = None) -> bool:
    """System-initiated fallback. Returns True if the mode actually changed."""
    state = get_state(db)
    if state.mode == TradingMode.PAPER:
        return False
    _set(db, state, TradingMode.PAPER, "system", reason, ip)
    return True


def switch_to_paper(db: Session, ip: str | None) -> None:
    """User one-click switch back to PAPER. Always allowed."""
    state = get_state(db)
    if state.mode != TradingMode.PAPER:
        _set(db, state, TradingMode.PAPER, "user", "user switched to PAPER", ip)


def on_startup(db: Session) -> None:
    """Every process start (restart/redeploy) forces PAPER."""
    fallback_to_paper(db, "auto-fallback: process restart")


def request_live(
    db: Session, user: User, password: str, totp_code: str, confirmation: str, ip: str | None
) -> None:
    state = get_state(db)

    def deny(reason: str, public: str):
        _audit(db, state.mode, TradingMode.LIVE, "user", f"denied: {reason}", False, ip)
        db.commit()
        log.warning("LIVE switch denied", extra={"reason": reason, "ip": ip})
        raise ModeSwitchDenied(reason, public)

    if state.mode == TradingMode.LIVE:
        return
    if confirmation != LIVE_CONFIRMATION_PHRASE:
        deny("confirmation_mismatch", 'Type "LIVE" exactly to confirm.')
    failure = verify_credentials(db, user, password, totp_code)
    if failure:
        deny(failure, "Re-authentication failed.")
    if state.halted:
        deny("halted", f"Trading is halted: {state.halt_reason}. Resume before going LIVE.")
    for name, check in _live_preconditions:
        err = check(db)
        if err:
            deny(f"precondition:{name}", err)
    _set(db, state, TradingMode.LIVE, "user", "user switched to LIVE (password+TOTP+confirmation)", ip)


def halt(db: Session, reason: str, actor: str, ip: str | None = None) -> None:
    """Global kill switch: stop all new orders and drop to PAPER."""
    state = get_state(db)
    state.halted = True
    state.halt_reason = reason
    db.commit()
    log.error("trading HALTED", extra={"reason": reason, "actor": actor})
    fallback_to_paper(db, f"halt: {reason}", ip)


def resume(db: Session, ip: str | None = None) -> None:
    """Clears the halt. Mode stays PAPER; going LIVE again needs the full LIVE flow."""
    state = get_state(db)
    state.halted = False
    state.halt_reason = None
    db.commit()
    log.warning("trading resumed (mode remains PAPER)", extra={"ip": ip})
