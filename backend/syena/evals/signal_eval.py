"""News-signal replay evals (17B) and the labelled outcome dataset.

The replay engine re-runs the exact live analysis on stored news, feeding it ONLY data available at the news
timestamp: candles are served from the cache and cut at `as_of`, and a spy raises if anything later leaks in.
"""
import logging
import random
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.evals import metrics as M
from syena.evals import runner
from syena.market.data import cached_candles
from syena.models import NewsAnalysis, NewsItem, NewsOutcome
from syena.news import pipeline

log = logging.getLogger("syena.evals")
COST_PCT = 0.25  # approx round-trip costs for a ₹10k delivery trade, in %


class LookaheadError(AssertionError):
    pass


class ReplayData:
    """Point-in-time market data from the candle cache. Any access beyond `as_of` raises."""

    def __init__(self, db: Session, as_of: datetime):
        self.db, self.as_of = db, as_of
        self.max_seen: datetime | None = None

    def _guard(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df[df.index <= pd.Timestamp(self.as_of)]
        if len(df):
            last = df.index[-1].to_pydatetime()
            if last > self.as_of:
                raise LookaheadError(f"candle {last} after as_of {self.as_of}")
            self.max_seen = max(self.max_seen or last, last)
        return df

    def candles(self, symbol, interval, start, end):
        return self._guard(cached_candles(self.db, symbol, interval, start, min(end, self.as_of)))

    def ltp(self, symbols):
        out = {}
        for s in symbols:
            df = self.candles(s, "15minute", self.as_of - timedelta(days=5), self.as_of)
            if df.empty:
                df = self.candles(s, "day", self.as_of - timedelta(days=10), self.as_of)
            if len(df):
                out[s] = float(df["close"].iloc[-1])
        return out

    def index_snapshot(self):
        snap = {}
        for name, sym in (("NIFTY", "NIFTY 50"), ("SENSEX", "SENSEX"), ("VIX", "INDIA VIX")):
            df = self.candles(sym, "day", self.as_of - timedelta(days=10), self.as_of)
            if len(df) >= 2:
                last, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
                snap[name] = {"last": last, "prev_close": prev, "change_pct": (last / prev - 1) * 100}
        return snap


def _price_at(df: pd.DataFrame, ts: datetime, after: bool = False) -> float | None:
    if df.empty:
        return None
    sel = df[df.index >= pd.Timestamp(ts)] if after else df[df.index <= pd.Timestamp(ts)]
    if sel.empty:
        return None
    return float(sel["close"].iloc[0] if after else sel["close"].iloc[-1])


def label_outcomes(db: Session, md, max_items: int = 200) -> int:
    """Build/extend the labelled dataset: for each analysed news item + stock, what price did 1h/1d/5d later."""
    n = 0
    rows = db.execute(select(NewsAnalysis, NewsItem).join(NewsItem, NewsItem.id == NewsAnalysis.news_item_id)
                      .where(NewsAnalysis.stage == "triage", NewsAnalysis.relevant.is_(True))
                      .order_by(NewsItem.published_at.desc()).limit(max_items)).all()
    for an, item in rows:
        for sym in an.symbols or []:
            existing = db.scalar(select(NewsOutcome).where(NewsOutcome.news_item_id == item.id, NewsOutcome.symbol == sym))
            if existing and existing.ret_5d is not None:
                continue
            ts = item.published_at
            try:
                intra = md.candles(sym, "15minute", ts - timedelta(days=3), ts + timedelta(days=2))
                daily = md.candles(sym, "day", ts - timedelta(days=10), ts + timedelta(days=12))
            except Exception as e:
                log.info("outcome candles unavailable", extra={"stock": sym, "error": str(e)})
                continue
            p0 = _price_at(intra, ts) or _price_at(daily, ts)
            if not p0:
                continue
            p1h = _price_at(intra, ts + timedelta(hours=1), after=True)
            after = daily[daily.index > pd.Timestamp(ts)]
            p1d = float(after["close"].iloc[0]) if len(after) >= 1 else None
            p5d = float(after["close"].iloc[4]) if len(after) >= 5 else None
            row = existing or NewsOutcome(news_item_id=item.id, symbol=sym, ts=ts, price_at=p0)
            row.ret_1h = None if p1h is None else round((p1h / p0 - 1) * 100, 4)
            row.ret_1d = None if p1d is None else round((p1d / p0 - 1) * 100, 4)
            row.ret_5d = None if p5d is None else round((p5d / p0 - 1) * 100, 4)
            if not existing:
                db.add(row)
            n += 1
    db.commit()
    return n


def compute_metrics(rows: list[dict], horizon: str, min_sample: int) -> dict:
    """rows: {pred, conf, ret_1h, ret_1d, ret_5d, nifty_ret}"""
    pairs, conf_hits, sig_returns = [], [], []
    for r in rows:
        actual = M.direction_label(r.get(f"ret_{horizon}"))
        if actual is None:
            continue
        pairs.append((r["pred"], actual))
        if r["pred"] in ("BUY", "SELL"):
            conf_hits.append((r["conf"], r["pred"] == actual))
            ret = r[f"ret_{horizon}"] if r["pred"] == "BUY" else -r[f"ret_{horizon}"]
            sig_returns.append(ret - COST_PCT)
    n = len(pairs)
    rnd = random.Random(42)
    rand_pairs = [(rnd.choice(M.LABELS), a) for _, a in pairs]
    buy_pairs = [("BUY", a) for _, a in pairs]
    directional = [(p, a) for p, a in pairs if p != "HOLD"]
    out = {
        "horizon": horizon, "n": n,
        "status": "insufficient data" if n < min_sample else "ok",
        "direction_accuracy": M.accuracy(pairs),
        "directional_hit_rate": M.accuracy(directional),
        "buy": M.precision_recall(pairs, "BUY"), "sell": M.precision_recall(pairs, "SELL"),
        "confusion": M.confusion(pairs),
        "calibration": M.calibration(conf_hits),
        "avg_return_per_signal_after_costs_pct": round(sum(sig_returns) / len(sig_returns), 4) if sig_returns else None,
        "n_signals": len(sig_returns),
        "baselines": {"random": M.accuracy(rand_pairs), "always_buy": M.accuracy(buy_pairs),
                      "nifty_avg_return_pct": _avg([r.get("nifty_ret") for r in rows])},
        "predicted_distribution": M.counts(p for p, _ in pairs),
    }
    return out


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _nifty_forward(db: Session, ts: datetime) -> float | None:
    """Nifty's next-day return over the same window (benchmark), from the candle cache."""
    df = cached_candles(db, "NIFTY 50", "day", ts - timedelta(days=7), ts + timedelta(days=7))
    before, after = df[df.index <= pd.Timestamp(ts)], df[df.index > pd.Timestamp(ts)]
    if before.empty or after.empty:
        return None
    return round((float(after["close"].iloc[0]) / float(before["close"].iloc[-1]) - 1) * 100, 4)


def replay(db: Session, model: str | None = None, limit: int = 100, eval_run_id: int | None = None) -> tuple[list[dict], list[dict]]:
    outcomes = list(db.scalars(select(NewsOutcome).where(NewsOutcome.ret_1d.is_not(None)).order_by(NewsOutcome.ts.desc()).limit(limit)))
    rows, cases = [], []
    for o in sorted(outcomes, key=lambda x: x.ts):
        item = db.get(NewsItem, o.news_item_id)
        items = [i for i in db.scalars(select(NewsItem).where(NewsItem.published_at <= o.ts,
                 NewsItem.published_at >= o.ts - timedelta(hours=24)))
                 if i.id == item.id or (o.symbol.lower() in (i.title or "").lower())][:5] or [item]
        md = ReplayData(db, o.ts)
        etype = db.scalar(select(NewsAnalysis.event_type).where(NewsAnalysis.news_item_id == item.id, NewsAnalysis.stage == "triage"))
        try:
            a = pipeline.analyse(db, o.symbol, items, md, o.ts, event_type=etype, model=model, eval_run_id=eval_run_id)
        except LookaheadError:
            raise
        except Exception as e:
            cases.append({"case_id": f"news{o.news_item_id}-{o.symbol}", "ok": False, "error": str(e)})
            continue
        if md.max_seen and md.max_seen > o.ts:
            raise LookaheadError(f"replay saw data up to {md.max_seen} for item at {o.ts}")
        row = {"pred": a["action"], "conf": a["confidence"], "ret_1h": o.ret_1h, "ret_1d": o.ret_1d, "ret_5d": o.ret_5d,
               "nifty_ret": _nifty_forward(db, o.ts)}
        rows.append(row)
        actual = M.direction_label(o.ret_1d)
        cases.append({"case_id": f"news{o.news_item_id}-{o.symbol}", "ok": actual == a["action"], "pred": a["action"],
                      "conf": a["confidence"], "actual": actual, "ret_1d": o.ret_1d, "title": item.title,
                      "reasoning": a["reasoning"]})
    return rows, cases


def run_suite(db: Session, config: dict | None = None, triggered_by: str = "manual"):
    config = config or {}
    model = config.get("model") or settings_store.get(db, "models")["analysis"]
    run = runner.start(db, "signal_replay", {"model": model, "limit": config.get("limit", 100),
                                             "prompt": "news_analysis"}, triggered_by)
    try:
        rows, cases = replay(db, model=model, limit=int(config.get("limit", 100)), eval_run_id=run.id)
        cfg = settings_store.get(db, "evals")
        metrics = {h: compute_metrics(rows, h, cfg["min_signal_sample"]) for h in ("1h", "1d", "5d")}
        for c in cases:
            runner.add_result(db, run, c["case_id"], c["ok"], score=c.get("conf"), input={"title": c.get("title")},
                              expected=c.get("actual"), actual={"pred": c.get("pred"), "reasoning": c.get("reasoning")},
                              detail=c.get("error"))
        m1d = metrics["1d"]
        passed = None if m1d["status"] != "ok" else bool(
            (m1d["direction_accuracy"]["value"] or 0) >= cfg["min_direction_accuracy"]
            and (m1d["calibration"]["ece"] is None or m1d["calibration"]["ece"] <= cfg["max_calibration_error"]))
        return runner.finish(db, run, metrics, passed, len(cases))
    except Exception as e:
        return runner.fail(db, run, e)
