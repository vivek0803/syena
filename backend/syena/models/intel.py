"""AI calls, news, evals, learning loop and portfolio tables."""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from syena.db import Base, UTCDateTime, utcnow


# ---------------------------------------------------------------- AI
class AICall(Base):
    __tablename__ = "ai_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    tier: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    prompt_name: Mapped[str] = mapped_column(String(48))
    prompt_version: Mapped[str] = mapped_column(String(16))
    prompt: Mapped[str] = mapped_column(Text)
    response_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)
    eval_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------- News
class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    dup_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="NEW")  # NEW|DUPLICATE|STALE|TRIAGED|ANALYSED|IRRELEVANT|ERROR|FAILED
    image_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)  # photos in Telegram posts


class NewsAnalysis(Base):
    __tablename__ = "news_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    news_item_id: Mapped[int] = mapped_column(ForeignKey("news_items.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(16))  # triage | analysis
    relevant: Mapped[bool] = mapped_column(Boolean, default=False)
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1..1
    event_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ai_call_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NewsOutcome(Base):
    """Labelled replay dataset: what price did after a news item (17B)."""

    __tablename__ = "news_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    news_item_id: Mapped[int] = mapped_column(ForeignKey("news_items.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    price_at: Mapped[float] = mapped_column(Float)
    ret_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    ret_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    ret_5d: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (UniqueConstraint("news_item_id", "symbol", name="uq_news_outcome"),)


# ---------------------------------------------------------------- Evals
class EvalDataset(Base):
    __tablename__ = "eval_datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    suite: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32))
    n_cases: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    suite: Mapped[str] = mapped_column(String(32), index=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # model, prompt_version, etc.
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    n_cases: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    triggered_by: Mapped[str] = mapped_column(String(16), default="manual")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(String(128))
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expected: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    actual: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class ManualLabel(Base):
    """Your labels used to spot-check the LLM judge, the reviewer, triage and mapping."""

    __tablename__ = "manual_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # triage | mapping | judge | review
    ref_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("kind", "ref_id", name="uq_manual_label"),)


# ---------------------------------------------------------------- Learning loop
class TradeReview(Base):
    __tablename__ = "trade_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    decision_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # closed_trade | rejected | skipped
    classification: Mapped[str] = mapped_column(String(32))
    decision_quality: Mapped[str] = mapped_column(String(16))  # sound | unsound
    outcome: Mapped[str] = mapped_column(String(16))  # win | loss | flat | avoided_loss | missed_gain
    right: Mapped[list | None] = mapped_column(JSON, nullable=True)
    wrong: Mapped[list | None] = mapped_column(JSON, nullable=True)
    counterfactual: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposed_lesson: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hindsight_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ai_call_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # L-001
    text: Mapped[str] = mapped_column(Text)
    trigger: Mapped[dict] = mapped_column(JSON)  # structured conditions
    action: Mapped[dict] = mapped_column(JSON)  # {"type": ..., "value": ...}
    status: Mapped[str] = mapped_column(String(12), default="PROPOSED", index=True)
    source: Mapped[str] = mapped_column(String(16))  # review | mining
    support_count: Mapped[int] = mapped_column(Integer, default=0)
    contradiction_count: Mapped[int] = mapped_column(Integer, default=0)
    win_rate_impact: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_validated: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class LessonEvidence(Base):
    __tablename__ = "lesson_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supports: Mapped[bool] = mapped_column(Boolean)
    day: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("lesson_id", "trade_id", "review_id", name="uq_lesson_evidence"),)


class LessonApplication(Base):
    __tablename__ = "lesson_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    decision_id: Mapped[int] = mapped_column(Integer, index=True)
    status_at_time: Mapped[str] = mapped_column(String(12))  # ACTIVE | TESTING
    applied: Mapped[bool] = mapped_column(Boolean)  # False = shadow "would have applied"
    changed_outcome: Mapped[bool] = mapped_column(Boolean, default=False)
    effect: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    was_right: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # scored later
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Portfolio X-Ray
class HoldingSnapshot(Base):
    __tablename__ = "holding_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)  # zerodha | angel | cas
    taken_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="OK")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("holding_snapshots.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))
    asset_class: Mapped[str] = mapped_column(String(16))  # equity | etf | mf
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    qty: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ltp: Mapped[float | None] = mapped_column(Float, nullable=True)
    value: Mapped[float] = mapped_column(Float)
    amc: Mapped[str | None] = mapped_column(String(128), nullable=True)
    folio: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amfi_code: Mapped[str | None] = mapped_column(String(16), nullable=True)


class MFSchemeHolding(Base):
    """Underlying holdings of a mutual fund scheme from the AMC's monthly portfolio disclosure."""

    __tablename__ = "mf_scheme_holdings"

    id: Mapped[int] = mapped_column(primary_key=True)
    scheme_key: Mapped[str] = mapped_column(String(64), index=True)  # scheme ISIN or AMFI code
    scheme_name: Mapped[str] = mapped_column(String(255))
    as_of: Mapped[date] = mapped_column(Date)
    holding_isin: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    holding_name: Mapped[str] = mapped_column(String(255))
    pct_nav: Mapped[float] = mapped_column(Float)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(16), default="equity")  # equity|debt|cash|other

    __table_args__ = (Index("ix_mfsh_scheme_asof", "scheme_key", "as_of"),)


class IsinMaster(Base):
    __tablename__ = "isin_master"

    isin: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mcap_bucket: Mapped[str | None] = mapped_column(String(8), nullable=True)  # large|mid|small


# ---------------------------------------------------------------- Owner's notes
class OwnerNote(Base):
    """Something the owner learned (macro links, sector views, rules of thumb). Syena's AI reads the relevant notes
    when it analyses news, answers questions, compares stocks, reviews a manual investment or a closed trade."""
    __tablename__ = "owner_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="general")  # macro | sector | stock | rule | general
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)  # always given to the AI
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    applies: Mapped[list] = mapped_column(JSON, default=list)  # areas: news, ask, compare, invest, review
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # Syena's one-line reading of it
    effects: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{when, then, direction}]
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sectors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    symbols: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    digest_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | ai | basic
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NoteUse(Base):
    __tablename__ = "note_uses"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("owner_notes.id", ondelete="CASCADE"), index=True)
    ai_call_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str | None] = mapped_column(String(200), nullable=True)  # headline / symbol / question it was used for
    influenced: Mapped[bool] = mapped_column(Boolean, default=False)  # the AI said the note changed its view
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
