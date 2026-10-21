"""Structured JSON logging with secret redaction and an optional DB sink (system_logs).

Usage: log = logging.getLogger("syena.mode"); log.info("msg", extra={"stock": "INFY", "ctx": {...}})
"""
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

SENSITIVE_KEY = re.compile(r"(pass(word)?|secret|token|totp|api[_-]?key|authorization|cookie|otp|pin)", re.I)
REDACTED = "***"
_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def redact(value, _depth: int = 0):
    """Recursively redact values whose key looks sensitive."""
    if _depth > 6:
        return value
    if isinstance(value, dict):
        return {k: (REDACTED if SENSITIVE_KEY.search(str(k)) else redact(v, _depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value]
    return value


class _SecretValueFilter(logging.Filter):
    """Scrubs known secret values from the rendered message as a last line of defence."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            msg = record.getMessage()
            for s in self.secrets:
                msg = msg.replace(s, REDACTED)
            record.msg, record.args = msg, ()
        return True


def _extras(record: logging.LogRecord) -> dict:
    return redact({k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class DBLogHandler(logging.Handler):
    """Writes syena.* records to system_logs so the UI can search/filter them."""

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("syena"):
            return
        try:
            from syena.db import SessionLocal
            from syena.models import SystemLog

            extras = _extras(record)
            stock = extras.pop("stock", None)
            algo_id = extras.pop("algo_id", None)
            with SessionLocal() as db:
                db.add(
                    SystemLog(
                        level=record.levelname,
                        module=record.name[:64],
                        message=record.getMessage(),
                        stock=stock,
                        algo_id=algo_id,
                        extra=json.loads(json.dumps(extras, default=str)) or None,
                    )
                )
                db.commit()
        except Exception:  # never let logging break the app
            self.handleError(record)


def configure_logging(level: str = "INFO", to_db: bool = True, secrets: list[str] | None = None) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_syena", False):
            root.removeHandler(h)
    secret_filter = _SecretValueFilter(secrets or [])

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(JsonFormatter())
    stream.addFilter(secret_filter)
    stream._syena = True  # type: ignore[attr-defined]
    root.addHandler(stream)

    log_dir = os.environ.get("SYENA_LOG_DIR")
    if log_dir:  # laptop setup: also write logs to a folder you (and Claude) can open
        try:
            from logging.handlers import RotatingFileHandler

            os.makedirs(log_dir, exist_ok=True)
            name = "worker" if "worker" in (sys.argv[0] if sys.argv else "") else "api"
            fh = RotatingFileHandler(os.path.join(log_dir, f"{name}.log"), maxBytes=2_000_000, backupCount=3,
                                     encoding="utf-8")
            fh.setFormatter(JsonFormatter())
            fh.addFilter(secret_filter)
            fh._syena = True  # type: ignore[attr-defined]
            root.addHandler(fh)
        except OSError:
            pass

    if to_db:
        dbh = DBLogHandler(level=logging.INFO)
        dbh.addFilter(secret_filter)
        dbh._syena = True  # type: ignore[attr-defined]
        root.addHandler(dbh)

    root.setLevel(level)
