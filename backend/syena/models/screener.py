"""Stock universe, daily prices + ratios, and fundamental screens."""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from syena.db import Base, UTCDateTime, utcnow


class Stock(Base):
    """Every NSE-listed equity, with its latest company figures (updated when results come out)."""

    __tablename__ = "stocks"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(80), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # company figures (rupees, per-share in rupees, ratios in %)
    shares_outstanding: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    book_value_ps: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_ps: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_income_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    ebitda_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_debt: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)
    roa: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_equity: Mapped[float | None] = mapped_column(Float, nullable=True)  # ratio, e.g. 0.45
    current_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    sales_growth: Mapped[float | None] = mapped_column(Float, nullable=True)  # % year on year (latest quarter)
    profit_growth: Mapped[float | None] = mapped_column(Float, nullable=True)  # % year on year (latest quarter)
    operating_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    insider_holding: Mapped[float | None] = mapped_column(Float, nullable=True)  # % held by promoters/insiders
    fundamentals_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # yahoo | csv
    fundamentals_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    fundamentals_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StockDaily(Base):
    """One row per stock per trading day: prices and the ratios computed from that day's close."""

    __tablename__ = "stock_daily"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    day: Mapped[date] = mapped_column(Date)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    w52_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    w52_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap_cr: Mapped[float | None] = mapped_column(Float, nullable=True)  # ₹ crore
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_ebitda: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    earnings_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_from_52w_high: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (UniqueConstraint("symbol", "day", name="uq_stock_day"), Index("ix_stock_daily_day", "day"))


class Screen(Base):
    __tablename__ = "screens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    conditions: Mapped[str] = mapped_column(Text)  # e.g. "roe > 15 and debt_to_equity < 0.5 and pe < 30"
    signal_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # technical rule, same language as algos
    signal_rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    algo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # keep this algo's stock list = screen members
    exit_on_removal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # sell when a stock leaves
    members: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ScreenRun(Base):
    __tablename__ = "screen_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    screen_id: Mapped[int] = mapped_column(ForeignKey("screens.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    ran_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    members: Mapped[list] = mapped_column(JSON, default=list)
    added: Mapped[list] = mapped_column(JSON, default=list)
    removed: Mapped[list] = mapped_column(JSON, default=list)
    signals: Mapped[list] = mapped_column(JSON, default=list)  # [{"symbol":..,"detail":..}]
    skipped_no_data: Mapped[int] = mapped_column(Integer, default=0)


class Watchlist(Base):
    """A list of stocks the user picked to compare side by side (Compare page)."""
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    ai_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # last AI comparison
    ai_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
