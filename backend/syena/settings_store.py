"""User-editable runtime settings (Settings page), stored in app_settings.

Each key has a default and a validator. Trading mode is NOT here (see services/mode.py).
"""
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from syena.db import utcnow
from syena.models import AppSetting

# Earlier default lists: a saved list identical to one of these is upgraded to the current default automatically.
OLD_DEFAULT_FEEDS = [
    {"https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml", "https://www.moneycontrol.com/rss/marketreports.xml",
     "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "https://www.livemint.com/rss/markets"},
    {"https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml", "https://www.moneycontrol.com/rss/buzzingstocks.xml", "https://www.moneycontrol.com/rss/latestnews.xml", "https://www.moneycontrol.com/rss/results.xml", "https://www.moneycontrol.com/rss/marketreports.xml", "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "https://www.livemint.com/rss/companies", "https://www.livemint.com/rss/markets", "https://www.business-standard.com/rss/markets-106.rss", "https://www.business-standard.com/rss/companies-101.rss", "https://news.google.com/rss/search?q=NSE+shares+(order+OR+results+OR+acquisition+OR+stake+OR+rating)+when:1d&hl=en-IN&gl=IN&ceid=IN:en"},
]
# Moneycontrol is left out: it blocks automated readers (403/502).
DEFAULT_FEEDS = [
    {"name": "NSE announcements", "url": "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"},
    {"name": "ET stocks news", "url": "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms"},
    {"name": "ET markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
    {"name": "Livemint companies", "url": "https://www.livemint.com/rss/companies"},
    {"name": "Livemint markets", "url": "https://www.livemint.com/rss/markets"},
    {"name": "Business Standard markets", "url": "https://www.business-standard.com/rss/markets-106.rss"},
    {"name": "Business Standard companies", "url": "https://www.business-standard.com/rss/companies-101.rss"},
    {"name": "Google News: NSE stocks", "url": "https://news.google.com/rss/search?q=NSE+shares+(order+OR+results+OR+acquisition+OR+stake+OR+rating)+when:1d&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News: Q2 results", "url": "https://news.google.com/rss/search?q=(Q2+results+OR+quarterly+results)+NSE+when:1d&hl=en-IN&gl=IN&ceid=IN:en"},
]

DEFAULTS: dict[str, Any] = {
    "sizing": {"min_amount": 7000.0, "max_amount": 10000.0},
    "risk": {
        "max_open_positions": 5,
        "max_daily_loss": 3000.0,  # ₹, realised + unrealised; breach => auto-halt
        "max_exposure_per_stock": 10000.0,
        "max_trades_per_day": 10,
        "max_consecutive_broker_errors": 3,
    },
    # News trades: the final score is a weighted blend (news / technicals / market, in %) and must reach
    # confidence_threshold. Technicals and the market no longer veto on their own: they only add or subtract weight.
    "trade_gate": {"confidence_threshold": 70, "weights": {"news": 80, "technicals": 15, "market": 5},
                   # liquidity: skip companies too small or too thinly traded to enter and exit cleanly (0 = off)
                   "min_market_cap_cr": 1000, "min_avg_turnover_cr": 2,
                   # the target must be at least this many times the distance to the stop-loss
                   "min_reward_risk": 1.5},
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
    # After-market orders: entries signalled while the market is closed are queued for the next open
    # (swing / long-term only). Limit = signal price + max_gap_pct, so a big gap-up is not chased;
    # unfilled AMO entries are cancelled at cancel_unfilled_after IST.
    # Signals while the market is closed wait in Syena's queue; Syena places them itself after `place_after` at the
    # next open (skipping the opening-minute spike), unless the price opened more than max_gap_pct against the trade.
    "amo": {"enabled": True, "place_after": "09:16", "max_gap_pct": 2.0, "cancel_unfilled_after": "10:00"},
    # Fundamental screener: company figures refresh a batch of stocks every night (whole universe ~weekly).
    "screener": {"fundamentals_per_night": 400, "fundamentals_max_age_days": 7, "prefer_csv_days": 100, "shareholding_per_night": 150,
                 "signal_lookback_days": 400},
    # Live market data for the AI: how many web searches each feature may make per answer (0 = Syena's data only).
    # Each search costs about $0.01 on top of the tokens.
    "ai_web": {"ask": 4, "compare": 4, "invest": 3, "news_analysis": 0},
    # Trailing stop-loss for news / algo share trades and all option trades (execution/trailing.py)
    "trailing": {"enabled": True, "equity": {"activate_r": 1.0, "distance_r": 1.0},
                 "options": {"activate_pct": 20.0, "trail_pct": 25.0}},
    # NIFTY daily options strategy (market/nifty_options.py), paper only
    "nifty_options": {"enabled": True, "lots": 1, "stop_pct": 25.0, "target_pct": 50.0, "min_days_to_expiry": 1,
                      "entry_start": "09:30", "entry_end": "11:30", "max_range_pct": 1.2, "pcr_min_call": 0.7, "pcr_max_put": 1.4},
    # Options (paper only): Syena buys calls (bullish) or puts (bearish), never writes options.
    "options": {"enabled": True, "news_enabled": True, "min_premium": 2000, "max_premium": 15000, "max_lots": 3,
                "max_open": 5, "max_per_day": 10, "stop_pct": 30, "target_pct": 60, "strike": "ATM",
                "min_days_to_expiry": 4, "max_hold_days": 5},
    "orders": {"prefer_limit": True, "limit_buffer_bps": 10.0, "market_protection_pct": 2.0, "max_retries": 3},
    "market_context": {"w_nifty": 0.5, "w_sensex": 0.2, "w_vix": 0.3, "nifty_scale_pct": 1.0, "vix_scale_pct": 5.0},
    "models": {
        "triage": "claude-haiku-5-5",
        "analysis": "claude-haiku-5-5",
        "review": "claude-haiku-5-5",
        "judge": "claude-haiku-5-5",
        "premium": "claude-opus-5-5",  # Ask AI and Compare: light use, so the strongest model (Fable 5.1 also works)
        "second_opinion": None,  # required to agree in LIVE when set
        "shadow_analysis": None,  # challenger model run in parallel, never traded
    },
    # USD per million tokens (input, output). Edit if Anthropic pricing changes.
    "model_pricing": {
        "claude-haiku-5-5": [0.10, 0.50],
        "claude-sonnet-5-5": [2.0, 10.0],
        "claude-opus-5-5": [4.0, 20.0],
        "claude-fable-5-1": [10.0, 50.0],
        "claude-haiku-4-5-20251001": [1.0, 5.0],
    },
    "ai_budget": {"daily_usd": 5.0},
    "monitor": {"scan_minutes": 30, "snooze_minutes": 60, "negative_news_sentiment": -0.5},
    "news": {
        "poll_minutes": 1,  # news goes stale after 15 minutes: check every minute
        "style": "SWING",
        # AI says SELL (and you don't hold it): sell short intraday (MIS) and buy back the same day by square-off.
        "short_selling": True,
        "max_items_per_run": 100,
        # News older than this (by its published time) is skipped: no AI call, no trade.
        "max_age_minutes": 120,  # news during market hours stays tradeable this long (never past the close)
        "carry_after_hours": True,  # after-hours / overnight news stays valid until the next open
        "carry_minutes_after_open": 15,  # ...until 09:30
        "priced_in_pct": 2.0,
        "max_day_move_pct": 5.0,  # don't chase: no news buy if already up this much today (or short if down)  # skip a trade when the price already moved this much in its direction since the news
        # Same stock + same kind of event already analysed within this window -> later reports are duplicates.
        "symbol_dedup_minutes": 60,
        "notify_holdings": True,
        "macro_mapping": True,  # broad news (sanctions, tariffs, crude, RBI...) -> the stocks it affects
        "macro_max_stocks": 5,
        "macro_min_confidence": 60,  # Telegram message when news about a stock you own is analysed  # same stock analysed in the last hour: another source's copy, no second AI call
        "ai_company_lookup": True,  # no company name matched: ask the AI which listed companies the item is about
        "fetch_articles": True,  # read the full article for relevant news when the feed only gives a headline
        "read_images": True,  # read text in Telegram post images with the AI (triage model)
        # candlestick patterns + trendlines shown to the AI for each analysed stock, per timeframe, over this many days
        "pattern_timeframes": ["day", "60minute", "15minute", "5minute", "minute"],
        "pattern_lookback_days": 30,
        "telegram_channels": ["moneypurseadv"],
        # Stock-specific sources first; general market commentary rarely names a company. A feed that fails is
        # shown on the News page (Sources) and can be switched off with "enabled": false.
        "feeds": DEFAULT_FEEDS,
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
    value = _merge(DEFAULTS[key], row.value if row else None)
    if key == "news" and isinstance(value.get("feeds"), list) and \
            {f.get("url") for f in value["feeds"]} in OLD_DEFAULT_FEEDS:
        value["feeds"] = DEFAULT_FEEDS  # still on the original four feeds: upgrade to the fuller default list
    return value


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
