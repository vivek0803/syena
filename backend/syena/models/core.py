"""Phase 1 data model: identity, sessions, audit, mode state, logs.

Later phases add their tables in new Alembic migrations.
"""
import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from syena.db import Base, UTCDateTime, utcnow


class TradingMode(str, enum.Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret_enc: Mapped[str] = mapped_column(Text)  # Fernet-encrypted
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)  # replay protection
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of cookie value
    csrf_token: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginAudit(Base):
    __tablename__ = "login_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    username_attempted: Mapped[str] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(64))  # internal only, never sent on failed login
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class SystemState(Base):
    """Single-row table (id=1). The only place trading mode is stored."""

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[TradingMode] = mapped_column(
        SAEnum(TradingMode, native_enum=False, length=8), default=TradingMode.PAPER
    )
    halted: Mapped[bool] = mapped_column(Boolean, default=False)
    halt_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ModeAudit(Base):
    __tablename__ = "mode_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_mode: Mapped[str] = mapped_column(String(8))
    to_mode: Mapped[str] = mapped_column(String(8))
    # "user" for an explicit switch, "system" for automatic fallbacks
    actor: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class SystemLog(Base):
    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String(10))
    module: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    stock: Mapped[str | None] = mapped_column(String(32), nullable=True)
    algo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_system_logs_ts", "ts"),
        Index("ix_system_logs_level_module", "level", "module"),
        Index("ix_system_logs_stock", "stock"),
    )
