"""Always-on worker: live prices (KiteTicker), algo runner, news pipeline, position monitor, learning loop,
online evals and Telegram summaries. Run with:  python -m syena.worker.main
"""
import logging
import signal
import threading
import time
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from syena import settings_store
from syena.algos import service as algo_service
from syena.config import get_settings
from syena.db import SessionLocal, utcnow
from syena.evals import online, signal_eval
from syena.execution import engine
from syena.learning import lessons, mining, reviewer
from syena.logging_setup import configure_logging
from syena.market import calendar
from syena.market import kite as kite_session
from syena.market.data import KiteMarketData
from syena.models import Algo, Instrument, Trade, TradeReview, TradeStatus, WorkerHeartbeat
from syena.monitor import check_price_levels, daily_summary, handle_news_sell_signals, scan, token_check, weekly_digest
from syena.news import pipeline
from syena.services import mode as mode_service

log = logging.getLogger("syena.worker")


# ---------------------------------------------------------------- live prices
class TickerService:
    """Keeps a KiteTicker connection subscribed to held + algo symbols; reconnects after token refresh."""

    def __init__(self):
        self.prices: dict[str, float] = {}
        self.updated_at = None
        self._ticker = None
        self._token = None
        self._subscribed: dict[int, str] = {}
        self._lock = threading.Lock()

    def wanted(self, db) -> dict[int, str]:
        syms = {t.stock for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN))}
        for a in db.scalars(select(Algo).where(Algo.status == "RUNNING", Algo.kind == "rule")):
            syms.update(a.symbols or [])
        out = {}
        for s in syms:
            tok = db.scalar(select(Instrument.instrument_token).where(Instrument.tradingsymbol == s, Instrument.exchange == "NSE"))
            if tok:
                out[tok] = s
        return out

    def ensure(self, db) -> None:
        row = kite_session.active_token(db)
        if row is None:
            self.stop()
            return
        from syena.security.crypto import decrypt

        access = decrypt(row.access_token_enc)
        if self._ticker is None or access != self._token:
            self.stop()
            self._start(access)
        want = self.wanted(db)
        with self._lock:
            new = [t for t in want if t not in self._subscribed]
            self._subscribed = want
        if new and self._ticker and self._ticker.is_connected():
            self._ticker.subscribe(new)
            self._ticker.set_mode(self._ticker.MODE_LTP, new)

    def _start(self, access: str) -> None:
        from kiteconnect import KiteTicker

        kt = KiteTicker(get_settings().kite_api_key, access, reconnect=True, reconnect_max_tries=300, reconnect_max_delay=30)

        def on_ticks(ws, ticks):
            with self._lock:
                for t in ticks:
                    sym = self._subscribed.get(t["instrument_token"])
                    if sym:
                        self.prices[sym] = float(t["last_price"])
                self.updated_at = utcnow()

        def on_connect(ws, _):
            toks = list(self._subscribed)
            if toks:
                ws.subscribe(toks)
                ws.set_mode(ws.MODE_LTP, toks)
            log.info("ticker connected", extra={"tokens": len(toks)})

        def on_close(ws, code, reason):
            log.warning("ticker closed", extra={"code": code, "reason": str(reason)})

        kt.on_ticks, kt.on_connect, kt.on_close = on_ticks, on_connect, on_close
        kt.connect(threaded=True)
        self._ticker, self._token = kt, access

    def stop(self) -> None:
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass
        self._ticker, self._token = None, None

    def fresh_prices(self, max_age_s: int = 90) -> dict[str, float]:
        if self.updated_at and (utcnow() - self.updated_at).total_seconds() <= max_age_s:
            return dict(self.prices)
        return {}


ticker = TickerService()


def _md(db):
    return KiteMarketData(db)


def _market_open(db) -> bool:
    return calendar.is_market_open(utcnow(), settings_store.get(db, "holidays"))


def job(name):
    """Wrap a job: own DB session, heartbeat, never crash the scheduler."""
    def deco(fn):
        def run():
            with SessionLocal() as db:
                try:
                    detail = fn(db)
                    hb = db.get(WorkerHeartbeat, name) or WorkerHeartbeat(name=name)
                    hb.ts, hb.detail = utcnow(), detail if isinstance(detail, dict) else {"result": str(detail)[:200]}
                    db.merge(hb)
                    db.commit()
                except kite_session.KiteNotConnected:
                    pass
                except Exception as e:
                    db.rollback()
                    log.exception("job failed", extra={"job": name, "error": str(e)})
        run.__name__ = name
        return run
    return deco


@job("fast_loop")
def fast_loop(db):
    ticker.ensure(db)
    if not _market_open(db):
        return {"market": "closed"}
    prices = ticker.fresh_prices()
    opens = list(db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN)))
    missing = [t.stock for t in opens if t.stock not in prices]
    if missing and kite_session.active_token(db):
        prices.update(_md(db).ltp(sorted(set(missing))))
    acts = check_price_levels(db, prices, broker_factory=engine.default_broker_factory(_md(db)))
    handle_news_sell_signals(db, utcnow() - timedelta(minutes=2), prices)
    return {"prices": len(prices), "actions": acts}


@job("algo_runner")
def algo_runner(db):
    if not _market_open(db) or not kite_session.active_token(db):
        return {"skipped": True}
    now = utcnow()
    ist = calendar.to_ist(now)
    md = _md(db)
    ran = 0
    for a in db.scalars(select(Algo).where(Algo.status == "RUNNING", Algo.kind == "rule")):
        bar = algo_service.BAR_MINUTES[a.timeframe]
        if a.timeframe == "day":
            if not (ist.hour == 9 and 20 <= ist.minute < 21):
                continue
        elif (ist.hour * 60 + ist.minute - 15) % bar != 0:  # bars align to 09:15
            continue
        try:
            algo_service.tick_algo(db, a, md, now, engine.default_broker_factory(md))
            ran += 1
        except Exception as e:
            log.error("algo tick failed", extra={"algo_id": a.id, "error": str(e)})
    return {"algos_run": ran}


@job("news")
def news_cycle(db):
    md = _md(db) if kite_session.active_token(db) else None
    if md is None:
        return {"skipped": "zerodha not connected (needed for prices/technicals)"}
    return pipeline.run_cycle(db, md, broker_factory=engine.default_broker_factory(md))


@job("monitor")
def monitor_scan(db):
    if not _market_open(db) or not kite_session.active_token(db):
        return {"skipped": True}
    md = _md(db)
    return scan(db, md, broker_factory=engine.default_broker_factory(md))


@job("token_check")
def token_job(db):
    token_check(db)
    return kite_session.token_status(db)


@job("reviews")
def reviews(db):
    md = _md(db) if kite_session.active_token(db) else None
    reviewed = {r for (r,) in db.execute(select(TradeReview.trade_id).where(TradeReview.trade_id.is_not(None)))}
    n = 0
    for t in db.scalars(select(Trade).where(Trade.status == TradeStatus.CLOSED).order_by(Trade.id.desc()).limit(50)):
        if t.id not in reviewed:
            reviewer.review_closed_trade(db, t, md)
            n += 1
    return {"reviewed": n}


@job("online_evals")
def online_evals(db):
    if not kite_session.active_token(db):
        return {"skipped": True}
    md = _md(db)
    return {"scored": online.score_signals(db, md), "unexecuted_reviewed": online.review_unexecuted(db),
            "lesson_apps_scored": online.score_lesson_applications(db)}


@job("daily_close")
def daily_close(db):
    out = {"promoted": lessons.promote_proposed(db), "retired": lessons.revalidate_active(db), "drift": online.drift_check(db)}
    for l in db.scalars(select(lessons.Lesson).where(lessons.Lesson.status == "TESTING")):
        lessons.validate_out_of_sample(db, l)
    if kite_session.active_token(db):
        out["labelled"] = signal_eval.label_outcomes(db, _md(db))
    return out


@job("daily_summary")
def summary_job(db):
    if calendar.is_trading_day(calendar.to_ist(utcnow()).date(), settings_store.get(db, "holidays")):
        daily_summary(db)
    return {"sent": True}


@job("weekly")
def weekly(db):
    codes = mining.propose_from_patterns(db, mining.mine(db))
    weekly_digest(db)
    return {"proposed": codes}


@job("instruments")
def instruments(db):
    if kite_session.active_token(db):
        return {"synced": _md(db).sync_instruments()}
    return {"skipped": True}


def build_scheduler() -> BlockingScheduler:
    tz = "Asia/Kolkata"
    s = BlockingScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60})
    with SessionLocal() as db:
        news_minutes = settings_store.get(db, "news")["poll_minutes"]
        scan_minutes = settings_store.get(db, "monitor")["scan_minutes"]
    s.add_job(fast_loop, "cron", second=5, id="fast_loop")
    s.add_job(algo_runner, "cron", minute="*", second=20, day_of_week="mon-fri", hour="9-15", id="algo_runner")
    s.add_job(news_cycle, "interval", minutes=news_minutes, id="news")
    s.add_job(monitor_scan, "cron", minute=f"*/{scan_minutes}", day_of_week="mon-fri", hour="9-15", id="monitor")
    s.add_job(token_job, "cron", minute="*/15", id="token_check")
    s.add_job(reviews, "cron", minute="*/10", id="reviews")
    s.add_job(online_evals, "cron", minute=7, id="online_evals")
    s.add_job(daily_close, "cron", hour=16, minute=30, id="daily_close")
    s.add_job(summary_job, "cron", hour=15, minute=45, day_of_week="mon-fri", id="daily_summary")
    s.add_job(weekly, "cron", day_of_week="sat", hour=10, minute=0, id="weekly")
    s.add_job(instruments, "cron", hour=8, minute=30, day_of_week="mon-fri", id="instruments")
    return s


def main() -> None:
    st = get_settings()
    configure_logging(st.log_level, to_db=st.log_to_db, secrets=st.secret_values())
    from syena.services.live_checks import register_live_checks

    register_live_checks()
    with SessionLocal() as db:
        mode_service.on_startup(db)
        pipeline.news_algo(db)
    sched = build_scheduler()

    def shutdown(*_):
        log.info("worker stopping")
        ticker.stop()
        sched.shutdown(wait=False)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log.info("worker started")
    while True:
        try:
            sched.start()
            break
        except Exception as e:  # pragma: no cover - restart-on-failure is also handled by docker
            log.exception("scheduler crashed; restarting in 10s", extra={"error": str(e)})
            time.sleep(10)


if __name__ == "__main__":
    main()
