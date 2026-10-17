"""TOTP (RFC 6238) with replay protection.

A code is accepted for the current 30s step or one step either side (clock drift),
but each step can only be used once: we remember the last accepted step per user.
"""
import time

import pyotp

from syena.models import User
from syena.security.crypto import decrypt, encrypt

ISSUER = "Syena"


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def encrypt_secret(secret: str) -> str:
    return encrypt(secret)


def verify_and_consume(user: User, code: str, now: float | None = None) -> bool:
    """Verify a code and mark its time step as used. Caller must commit the session."""
    code = (code or "").strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        return False
    totp = pyotp.TOTP(decrypt(user.totp_secret_enc))
    now = time.time() if now is None else now
    current = int(now // totp.interval)
    for step in (current - 1, current, current + 1):
        if step <= (user.totp_last_step or 0):
            continue  # already used (or older than a used step): replay
        if pyotp.utils.strings_equal(totp.generate_otp(step), code):
            user.totp_last_step = step
            return True
    return False
