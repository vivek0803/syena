"""Turning a view on a stock or index into a (paper) option trade.

Bullish -> buy a call, bearish -> buy a put. Syena only BUYS options: the most it can lose is the premium paid, and
no margin is blocked. The contract: the nearest expiry with at least `min_days_to_expiry` days left (so time decay
doesn't eat the trade in its first days), at the money by default (or one strike in / out of the money).
Stop and target are on the option's premium (default -30% / +60%), and the position is closed by 15:15 on the
trading day before expiry at the latest, or after max_hold_days, whichever comes first.
"""
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.db import utcnow
from syena.execution import engine
from syena.execution.checks import Check
from syena.market import calendar
from syena.market import options as op
from syena.models import Algo, OptionContract, Style

log = logging.getLogger("syena.options")


def has_options(db: Session, underlying: str) -> bool:
    return db.scalar(select(OptionContract.id).where(OptionContract.underlying == underlying).limit(1)) is not None


def pick_contract(db: Session, underlying: str, direction: str, spot: float, now: datetime,
                  expiry: date | None = None, strike_pref: str | None = None) -> OptionContract | None:
    """direction BUY -> call, SELL -> put. strike_pref: ATM | ITM | OTM (one strike from the money)."""
    cfg = settings_store.get(db, "options")
    kind = "CE" if direction == "BUY" else "PE"
    min_days = float(cfg.get("min_days_to_expiry") or 0)
    exps = [e for e in op.expiries(db, underlying) if op.years_to_expiry(e, now) * 365 >= min_days]
    if expiry:
        exps = [e for e in exps if e == expiry] or [expiry]
    if not exps:
        return None
    exp = exps[0]
    cs = list(db.scalars(select(OptionContract).where(OptionContract.underlying == underlying, OptionContract.expiry == exp,
                                                      OptionContract.option_type == kind)))
    if not cs:
        return None
    cs.sort(key=lambda c: c.strike)
    i = min(range(len(cs)), key=lambda k: abs(cs[k].strike - spot))
    pref = (strike_pref or cfg.get("strike") or "ATM").upper()
    step = {"ATM": 0, "ITM": -1 if kind == "CE" else 1, "OTM": 1 if kind == "CE" else -1}.get(pref, 0)
    return cs[max(0, min(len(cs) - 1, i + step))]


def exit_deadline(db: Session, expiry: date) -> datetime:
    """15:00 on the trading day before expiry (the monitor's 15:00 scan closes it)."""
    holidays = settings_store.get(db, "holidays")
    d = expiry - timedelta(days=1)
    while not calendar.is_trading_day(d, holidays):
        d -= timedelta(days=1)
    return datetime.combine(d, time(15, 0), calendar.IST)


def news_options_algo(db: Session) -> Algo:
    a = db.scalar(select(Algo).where(Algo.kind == "news_opt"))
    if a is None:
        a = Algo(name="News options strategy", kind="news_opt", instrument="OPT", style=Style.SWING, run_mode="PAPER", status="RUNNING",
                 english_text="AI news verdicts traded with options: BUY -> buy a call, SELL -> buy a put (paper)", symbols=[], timeframe="day")
        db.add(a)
        db.commit()
    return a


def enter_option(db: Session, md, underlying: str, direction: str, *, source: str, algo_id: int | None = None,
                 signal_id: int | None = None, confidence: float | None = None, reasoning: dict | None = None,
                 inputs: dict | None = None, extra_checks: list[Check] | None = None, idempotency_key: str | None = None,
                 lots: int | None = None, expiry: date | None = None, strike: float | None = None, option_type: str | None = None,
                 stop_pct: float | None = None, target_pct: float | None = None, news_item_ids: list | None = None,
                 setup: dict | None = None, now: datetime | None = None, broker_factory=None):
    """Pick the contract, price it and send it through the normal execution engine (checks, decision log, paper fill)."""
    now = now or utcnow()
    cfg = settings_store.get(db, "options")
    try:
        op.ensure_contracts(db, md)
    except Exception as e:  # keep yesterday's list rather than miss the trade
        db.rollback()
        log.warning("option list not refreshed", extra={"error": str(e)[:200]})
    spot = op.spot(md, underlying)
    if strike is not None and option_type:
        c = db.scalar(select(OptionContract).where(OptionContract.underlying == underlying, OptionContract.expiry == expiry,
                                                   OptionContract.strike == strike, OptionContract.option_type == option_type))
        direction = "BUY" if option_type == "CE" else "SELL"
    else:
        c = pick_contract(db, underlying, direction, spot or 0, now, expiry) if spot else None
    base = dict(style=Style.SWING, source=source, algo_id=algo_id, signal_id=signal_id, confidence=confidence,
                reasoning=reasoning or {}, inputs=inputs or {}, news_item_ids=news_item_ids or [], setup=setup or {})
    if c is None:
        return engine.record_verdict(db, engine.TradeIntent(symbol=underlying, ref_price=spot or 0.0, instrument="OPT", **base),
                                     engine.DecisionType.REJECTED, f"REJECTED: no suitable {underlying} option contract",
                                     [Check("option_contract", False, "no price for the underlying" if not spot else
                                            f"no {'call' if direction == 'BUY' else 'put'} expiring in {cfg.get('min_days_to_expiry')}+ days")])
    q = op.quotes(md, [c]).get(c.token) or {}
    prem = q.get("ask") or q.get("ltp")
    sp, tp = float(stop_pct if stop_pct is not None else cfg["stop_pct"]), float(target_pct if target_pct is not None else cfg["target_pct"])
    t = op.years_to_expiry(c.expiry, now)
    iv = op.implied_vol(q.get("ltp"), spot, c.strike, t, c.option_type) if spot and q.get("ltp") else None
    g = op.greeks(spot, c.strike, t, iv, c.option_type) if iv else {}
    deadline = exit_deadline(db, c.expiry)
    hold = min(deadline, calendar.add_trading_days(now, int(cfg.get("max_hold_days") or 5), settings_store.get(db, "holidays"))
               - timedelta(minutes=30))
    info = {"underlying": underlying, "expiry": c.expiry.isoformat(), "strike": c.strike, "type": c.option_type,
            "lot_size": c.lot_size, "spot_at_entry": spot, "iv_at_entry": round(iv * 100, 1) if iv else None,
            "delta_at_entry": g.get("delta"), "days_to_expiry": round(t * 365, 1)}
    checks = list(extra_checks or [])
    checks.append(Check("option_quote", bool(prem), f"{c.tradingsymbol}: ask {q.get('ask')} / LTP {q.get('ltp')}" if prem else f"no price for {c.tradingsymbol}"))
    if q.get("bid") and q.get("ask"):
        spread = (q["ask"] - q["bid"]) / q["ask"] * 100
        checks.append(Check("option_spread", spread <= 5, f"bid-ask spread {spread:.1f}% of the premium (max 5%)"))
    intent = engine.TradeIntent(symbol=c.tradingsymbol, ref_price=float(prem or 0), instrument="OPT", option=info, lots=lots,
                                stop_loss=round(prem * (1 - sp / 100), 2) if prem else None,
                                target=round(prem * (1 + tp / 100), 2) if prem else None, auto_sell=True,
                                max_hold_until=hold, idempotency_key=idempotency_key, extra_checks=checks,
                                exit_condition=f"premium -{sp:g}% stop / +{tp:g}% target; out by {calendar.to_ist(hold):%d %b %H:%M} at the latest (expiry {c.expiry:%d %b})",
                                algo_condition=f"{'Bullish' if direction == 'BUY' else 'Bearish'} on {underlying}: buy {c.strike:g} "
                                               f"{'call' if c.option_type == 'CE' else 'put'} expiring {c.expiry:%d %b}", **base)
    return engine.enter(db, intent, broker_factory or engine.default_broker_factory(md), now=now)
