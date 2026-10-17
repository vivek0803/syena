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

    # Brokers / data
    kite_api_key: str = ""
    kite_api_secret: SecretStr = Field(default=SecretStr(""))
    angel_api_key: str = ""

    # AI
    anthropic_api_key: SecretStr = Field(default=SecretStr(""))

    # Telegram
    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_chat_id: str = ""
    telegram_webhook_secret: SecretStr = Field(default=SecretStr(""))
    # webhook: Telegram calls https://<domain>/api/telegram/webhook (server)
    # polling: the worker fetches updates every few seconds (laptop, no public URL needed)
    telegram_delivery: str = "webhook"

    # Golden eval datasets (defaults to <repo>/evals)
    evals_dir: str = ""

    # Logging
    log_level: str = "INFO"
    log_to_db: bool = True

    def secret_values(self) -> list[str]:
        """All secret values, scrubbed from log output as a last line of defence."""
        return [v.get_secret_value() for v in (self.encryption_key, self.kite_api_secret, self.anthropic_api_key,
                                               self.telegram_bot_token, self.telegram_webhook_secret) if v.get_secret_value()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
