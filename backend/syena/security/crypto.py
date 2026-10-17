"""Symmetric encryption for secrets at rest (TOTP seeds, broker access tokens)."""
from cryptography.fernet import Fernet, InvalidToken

from syena.config import get_settings


class EncryptionKeyMissing(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().encryption_key.get_secret_value()
    if not key:
        raise EncryptionKeyMissing("SYENA_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:  # wrong key or tampered data
        raise ValueError("unable to decrypt secret") from exc
