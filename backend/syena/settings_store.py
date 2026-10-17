"""User-editable runtime settings (Settings page), stored in app_settings.

Each key has a default and a validator. Trading mode is NOT here (see services/mode.py).
"""
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.models import AppSetting

DEFAULTS: dict[str, Any] = {
    "sizing": {"min_amount": 7000.0, "max_amount": 10000.0},
    "risk": {
        "max_open_positions": 5,
        "max_daily_loss": 3000.0,  # ₹, realised + unrealised; breach => auto-halt
        "max_exposure_per_stock": 10000.0,
        "max_trades_per_day": 10,
        "max_consecutive_broker_errors": 3,
    },
    "trade_gate": {"confidence_threshold": 70, "require_technical_agreement": True, "min_market_context": -0.3},
    "styles": {
        "intraday_entry_cutoff": "14:45",
        "intraday_square_off": "15:10",
        "intraday_default_stop_atr": 1.0,
        "intraday_default_target_atr": 2.0,
        "swing_default_stop_atr": 2.0,
        "swing_default_target_atr": 4.0,
        "swing_max_holding_days": 20,
        "swing_gap_down_exit_pct": 4.0,
    },
    "paper": {"starting_balance": 100000.0, "slippage_bps": 5.0},
    "orders": {"prefer_limit": True, "limit_buffer_bps": 10.0, "market_protection_pct": 2.0, "max_retries": 3},
    "market_context": {"w_nifty": 0.5, "w_sensex": 0.2, "w_vix": 0.3, "nifty_scale_pct": 1.0, "vix_scale_pct": 5.0},
    "models": {
        "triage": "claude-haiku-5-5",
        "analysis": "claude-sonnet-5-5",
        "review": "claude-opus-5-5",
        "judge": "claude-sonnet-5-5",
        "second_opinion": None,  # required to agree in LIVE when set
        "shadow_analysis": None,  # challenger model run in parallel, never traded
    },
    # USD per million tokens (input, output). Edit if Anthropic pricing changes.
    "model_pricing": {
        "claude-haiku-5-5": [0.10, 0.50],
        "claude-sonnet-5-5": [2.0, 10.0],
        "claude-opus-5-5": [4.0, 20.0],
        "claude-haiku-4-5-20251001": [1.0, 5.0],
    },
    "ai_budget": {"daily_usd": 5.0},
    "monitor": {"scan_minutes": 30, "snooze_minutes": 60, "negative_news_sentiment": -0.5},
    "news": {
        "poll_minutes": 5,
        "style": "SWING",
        "max_items_per_run": 40,
        "max_age_hours": 6,
        "feeds": [
            {"name": "NSE announcements", "url": "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"},
            {"name": "Moneycontrol markets", "url": "https://www.moneycontrol.com/rss/marketreports.xml"},
            {"name": "Economic Times markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
            {"name": "Livemint markets", "url": "https://www.livemint.com/rss/markets"},
        ],
        "watchlist": ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT", "BHARTIARTL", "AXISBANK"],
    },
    "evals": {
        "parser_min_accuracy": 0.9,
        "parser_min_clarification_accuracy": 0.9,
        "min_signal_sample": 30,
        "min_direction_accuracy": 0.55,
        "max_calibration_error": 0.15,
        "drift_accuracy_drop": 0.1,
        "drift_cost_spike_ratio": 2.0,
    },
    "live_gate": {
        "min_backtest_trades": 20,
        "min_backtest_win_rate": 0.45,
        "min_backtest_sharpe": 0.5,
        "max_backtest_drawdown_pct": 20.0,
        "min_paper_trades": 15,
        "min_paper_days": 10,
        "paper_vs_replay_tolerance": 0.15,  # |paper win rate - replay win rate|
    },
    "lessons": {
        "min_support_for_testing": 5,
        "min_distinct_days": 3,
        "revalidate_days": 30,
        "max_active": 25,
        "context_token_budget": 1500,
        "min_oos_improvement": 0.0,
    },
    "telegram": {"daily_summary_time": "15:45", "weekly_digest_day": "sat"},
    "holidays": [
        "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
        "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
        "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
    ],
    "portfolio": {"max_single_stock_pct": 10.0, "max_sector_pct": 30.0, "max_top10_pct": 60.0},
}

# Keys lessons/AI must never touch (spec 18C): enforced by learning.lessons.validate_action as well.
HUMAN_ONLY_KEYS = {"sizing", "risk", "live_gate"}


def _merge(default: Any, stored: Any) -> Any:
    if isinstance(default, dict) and isinstance(stored, dict):
        out = deepcopy(default)
        for k, v in stored.items():
            out[k] = _merge(default.get(k), v) if k in default else v
        return out
    return deepcopy(stored if stored is not None else default)


def get(db: Session, key: str) -> Any:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.get(AppSetting, key)
    return _merge(DEFAULTS[key], row.value if row else None)


def get_all(db: Session) -> dict[str, Any]:
    return {k: get(db, k) for k in DEFAULTS}


def _validate(key: str, value: Any) -> None:
    d = DEFAULTS[key]
    if isinstance(d, dict) and not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    if isinstance(d, list) and not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    if key == "sizing":
        lo, hi = float(value.get("min_amount", 0)), float(value.get("max_amount", 0))
        if not (0 < lo <= hi):
            raise ValueError("sizing: need 0 < min_amount <= max_amount")
    if key == "risk":
        for k in ("max_open_positions", "max_daily_loss", "max_exposure_per_stock", "max_trades_per_day"):
            if k in value and float(value[k]) <= 0:
                raise ValueError(f"risk.{k} must be positive")
    if key == "trade_gate" and "confidence_threshold" in value:
        if not 0 <= float(value["confidence_threshold"]) <= 100:
            raise ValueError("confidence_threshold must be 0-100")


def put(db: Session, key: str, value: Any, actor: str = "user") -> Any:
    """Only the authenticated Settings API calls this with actor='user'."""
    if key not in DEFAULTS:
        raise KeyError(key)
    if key in HUMAN_ONLY_KEYS and actor != "user":
        raise PermissionError(f"{key} can only be changed by the user")
    _validate(key, value)
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_at=utcnow()))
    else:
        row.value, row.updated_at = value, utcnow()
    db.commit()
    return get(db, key)
