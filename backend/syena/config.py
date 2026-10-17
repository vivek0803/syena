"""Application configuration, loaded from environment variables only.

Secrets live in the environment (or encrypted DB storage) and are never logged.
Note: there is deliberately NO setting that controls trading mode. Mode lives in
the database and can only be changed through the authenticated API.
"""
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYENA_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = "sqlite:///./syena.db"

    # Fernet key used to encrypt TOTP secrets, broker tokens, etc.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: SecretStr = Field(default=SecretStr(""))

    # Public origin of the app, e.g. https://syena.example.com. Used for CORS and Origin checks.
    public_origin: str = "http://localhost:5173"

    # Set true behind Caddy so the client IP is read from X-Forwarded-For.
    trust_proxy_headers: bool = False

    # Session cookie
    cookie_secure: bool = True
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12

    # Login protection
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    login_ip_rate_per_minute: int = 10

    # Logging
    log_level: str = "INFO"
    log_to_db: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
