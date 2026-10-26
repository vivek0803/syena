"""Pattern lab: every bullish pattern x filter backtested across stocks, judged on both halves of the period."""
from syena.algos import patternlab
from syena.models import Algo
from tests.conftest import make_candles


class MD:
    def candles(self, sym, iv, start, end):
        return make_candles(n=900, seed=sum(map(ord, sym)))


def test_lab_tests_every_combination_and_saves(db):
    out = patternlab.run(db, MD(), symbols=["AAA", "BBB", "CCC"], years=3)
    assert len(out["results"]) == len(patternlab.PATTERNS_TESTED) * len(patternlab.FILTERS)
    assert out["stocks"] == 3 and out["period"]["split"] > out["period"]["from"]
    r = out["results"][0]
    assert {"trades", "win_rate", "avg_return_pct", "early", "late", "works", "pattern_label", "filter_label"} <= set(r) or r["trades"] == 0
    assert patternlab.latest(db)["at"] == out["at"]
    for row in out["results"]:  # "works" demands enough trades and both halves positive
        if row["works"]:
            assert row["trades"] >= patternlab.MIN_TRADES and row["early"]["avg_return_pct"] > 0 and row["late"]["avg_return_pct"] > 0


def test_rules_are_valid_and_readable():
    for p in patternlab.PATTERNS_TESTED:
        for f in patternlab.FILTERS:
            rule = patternlab.rule_for(p, f)
            assert rule["stop_loss"] == {"type": "atr", "mult": 1.5, "n": 14}
            assert "Buy the next morning" in patternlab.english(p, f)


def test_winner_becomes_a_draft_daily_algo(db):
    a, readable = patternlab.create_algo(db, "bullish_engulfing", "support_uptrend", ["AAA", "BBB"])
    got = db.get(Algo, a.id)
    assert got.status == "DRAFT" and got.timeframe == "day" and got.symbols == ["AAA", "BBB"]
    assert "Support zone" in readable and "SMA" in readable.upper()


def test_lab_api(authed):
    r = authed.get("/api/pattern-lab").json()
    assert r["latest"] is None and "nifty200" in r["universes"]
    assert authed.post("/api/pattern-lab/algo", json={"pattern": "hammer", "filter": "alone"}).status_code == 404
