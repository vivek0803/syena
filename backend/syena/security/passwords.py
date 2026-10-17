from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2id with OWASP-recommended parameters (m=64 MiB, t=3, p=4)
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)

# Pre-computed hash used to equalise timing when the username does not exist.
_DUMMY_HASH = _hasher.hash("syena-dummy-password-for-timing")

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
