"""Trading domain: algos, signals, decisions, trades, orders, wallet, market data, broker tokens."""
import enum
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from syena.db import Base, UTCDateTime, utcnow


def _enum(e):
    return SAEnum(e, native_enum=False, length=16)


class Style(str, enum.Enum):
    INTRADAY = "INTRADAY"  # Kite product MIS
    SWING = "SWING"  # Kite product CNC
    LONG_TERM = "LONG_TERM"  # Kite product CNC, held for months/years: no gap-down exit, no default holding limit


class Side(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class DecisionType(str, enum.Enum):
    PLACED = "PLACED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"
    HELD = "HELD"  # an exit order is still working at the broker
    HOLD = "HOLD"  # (older records) the AI's verdict on news was HOLD; new ones are REJECTED with that reason
    ERROR = "ERROR"  # something failed (no price data, AI/broker error): nothing was decided
    EXITED = "EXITED"


class TradeStatus(str, enum.Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    PLACED = "PLACED"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Algo(Base):
    __tablename__ = "algos"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(8), default="rule")  # rule | news (news strategy is an algo too)
    english_text: Mapped[str] = mapped_column(Text)
    parsed_rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parser_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # deterministic | llm
    parser_prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    style: Mapped[Style] = mapped_column(_enum(Style))
    run_mode: Mapped[str] = mapped_column(String(10), default="PAPER")  # BACKTEST | PAPER | LIVE (requested)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")  # DRAFT|CONFIRMED|RUNNING|STOPPED
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(16), default="15minute")
    backtest_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    backtest_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    live_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # passed 17H gate + user enabled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(16))  # algo | news | manual | monitor
    algo_id: Mapped[int | None] = mapped_column(ForeignKey("algos.id"), nullable=True)
    news_analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(8))  # BUY | SELL | HOLD
    confidence: Mapped[float] = mapped_column(Float, default=0)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    cited: Mapped[list | None] = mapped_column(JSON, nullable=True)
    indicators: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    market_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)  # challenger model: logged, never traded
    price_at_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    ret_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    ret_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    ret_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    type: Mapped[DecisionType] = mapped_column(_enum(DecisionType))
    mode: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str | None] = mapped_column(String(4), nullable=True)
    algo_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    reason_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checks_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    inputs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lessons_consulted: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lessons_applied: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lessons_would_apply: Mapped[list | None] = mapped_column(JSON, nullable=True)
    similar_trades: Mapped[list | None] = mapped_column(JSON, nullable=True)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    algo_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # algo | news | manual
    mode: Mapped[str] = mapped_column(String(8))  # PAPER | LIVE (fixed at entry)
    style: Mapped[Style] = mapped_column(_enum(Style))
    algo_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    stock: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(4), default="BUY")
    qty: Mapped[int] = mapped_column(Integer)
    invested_amount: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price_defined: Mapped[str | None] = mapped_column(Text, nullable=True)  # exit condition text
    exit_price_executed: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    charges: Mapped[float] = mapped_column(Float, default=0)
    profit_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[TradeStatus] = mapped_column(_enum(TradeStatus), default=TradeStatus.PENDING)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    max_hold_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    auto_sell: Mapped[bool] = mapped_column(Boolean, default=False)
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    news_item_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    setup: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # features at entry, for learning
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(8))
    broker: Mapped[str] = mapped_column(String(8))  # paper | kite
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(8))  # LIMIT | MARKET
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    product: Mapped[str] = mapped_column(String(4))  # MIS | CNC
    purpose: Mapped[str] = mapped_column(String(8))  # ENTRY | EXIT
    variety: Mapped[str] = mapped_column(String(8), default="regular", server_default="regular")  # regular | amo
    status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus), default=OrderStatus.PENDING)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    charges: Mapped[float] = mapped_column(Float, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PaperWallet(Base):
    __tablename__ = "paper_wallet"

    id: Mapped[int] = mapped_column(primary_key=True)
    starting_balance: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Instrument(Base):
    __tablename__ = "instruments"

    instrument_token: Mapped[int] = mapped_column(Integer, primary_key=True)
    tradingsymbol: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exchange: Mapped[str] = mapped_column(String(8))
    segment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    instrument_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tick_size: Mapped[float] = mapped_column(Float, default=0.05)
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    updated_on: Mapped[date] = mapped_column(Date)


class Candle(Base):
    __tablename__ = "candles_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("symbol", "interval", "ts", name="uq_candle"),
        Index("ix_candles_lookup", "symbol", "interval", "ts"),
    )


class BrokerToken(Base):
    """Encrypted broker session tokens (Kite daily access token, Angel JWT)."""

    __tablename__ = "broker_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(String(16), index=True)  # kite | angel
    access_token_enc: Mapped[str] = mapped_column(Text)
    extra_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class OAuthNonce(Base):
    """One-time nonce carried through the Kite login redirect (SameSite=Strict cookies are not sent on it)."""

    __tablename__ = "oauth_nonces"

    id: Mapped[int] = mapped_column(primary_key=True)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # sell | token | drift | summary | info | halt
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="SENT")  # SENT|APPROVED|REJECTED|EXPIRED|FAILED
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | float | int | str | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Event(Base):
    """Append-only stream that the SSE endpoint tails (works across API and worker processes)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
