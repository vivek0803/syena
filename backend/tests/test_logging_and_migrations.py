import json
import logging
import pathlib

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select

from syena.db import Base, make_engine
from syena.logging_setup import DBLogHandler, JsonFormatter, _SecretValueFilter, redact
from syena.models import SystemLog

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def test_redact_nested_sensitive_keys():
    out = redact({"user": "v", "password": "p", "nested": {"api_key": "k", "access_token": "t", "ok": 1}})
    assert out == {"user": "v", "password": "***", "nested": {"api_key": "***", "access_token": "***", "ok": 1}}


def test_json_formatter_redacts_extras_and_secret_values():
    rec = logging.LogRecord("syena.kite", logging.INFO, "", 0, "token is SUPERSECRET123", (), None)
    rec.access_token = "abc"
    rec.stock = "INFY"
    _SecretValueFilter(["SUPERSECRET123"]).filter(rec)
    payload = json.loads(JsonFormatter().format(rec))
    assert payload["msg"] == "token is ***"
    assert payload["access_token"] == "***" and payload["stock"] == "INFY"


def test_db_log_handler_writes_searchable_rows(db):
    log = logging.getLogger("syena.test")
    h = DBLogHandler()
    log.addHandler(h)
    log.setLevel(logging.INFO)
    try:
        log.info("signal fired", extra={"stock": "TCS", "algo_id": 7, "ctx": {"rsi": 28}})
    finally:
        log.removeHandler(h)
    row = db.scalar(select(SystemLog))
    assert (row.stock, row.algo_id, row.module) == ("TCS", 7, "syena.test")
    assert row.extra == {"ctx": {"rsi": 28}}


def test_logs_api_filters(authed, db):
    db.add_all([SystemLog(level="INFO", module="syena.a", message="hello", stock="INFY"),
                SystemLog(level="ERROR", module="syena.b", message="boom", stock="TCS")])
    db.commit()
    assert [r["message"] for r in authed.get("/api/logs?level=error").json()] == ["boom"]
    assert [r["message"] for r in authed.get("/api/logs?stock=infy").json()] == ["hello"]
    assert [r["message"] for r in authed.get("/api/logs?q=boo").json()] == ["boom"]


def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{tmp_path}/mig.db"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models drifted from migrations
    tables = set(inspect(make_engine(url)).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    command.downgrade(cfg, "base")
