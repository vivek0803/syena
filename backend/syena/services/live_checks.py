"""Preconditions that must hold before the system can switch to LIVE (registered at startup)."""
from syena.market import kite
from syena.services.mode import _live_preconditions, register_live_precondition


def register_live_checks() -> None:
    names = {n for n, _ in _live_preconditions}
    if "kite_token" not in names:
        register_live_precondition("kite_token", kite.live_precondition)
