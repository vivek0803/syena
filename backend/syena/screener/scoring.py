"""Fixed-formula scores for the Compare page: the same figures always give the same ranking.

Each metric is ranked against the other stocks in the list (percentile 0-100, best = 100), metrics are averaged into
categories, and categories are weighted by horizon. A missing figure counts as unknown: the category is averaged over
what is known, a fully unknown category counts as 50 (neutral), and stocks with little data lose a few points.
The AI then explains the result and may nudge a score by at most 10 points for something the formula can't see
(e.g. a bank's debt/equity, a one-off jump in profit), saying why.
"""
import math

# metric -> (higher_is_better, label)
METRICS = {
    "roe": (True, "ROE"), "roa": (True, "ROA"), "opm": (True, "operating margin"), "npm": (True, "net margin"),
    "sales_growth": (True, "sales growth"), "profit_growth": (True, "profit growth"),
    "pe": (False, "P/E"), "pb": (False, "P/B"), "ev_ebitda": (False, "EV/EBITDA"), "earnings_yield": (True, "earnings yield"),
    "debt_to_equity": (False, "debt/equity"), "current_ratio": (True, "current ratio"),
    "ret_1m": (True, "1-month return"), "ret_3m": (True, "3-month return"), "ret_6m": (True, "6-month return"),
    "vs_dma50": (True, "% vs 50-day avg"), "vs_dma200": (True, "% vs 200-day avg"), "rsi_fit": (True, "RSI near 60"),
    "volatility": (False, "volatility"), "max_drawdown": (True, "max fall from peak"), "market_cap": (True, "size/liquidity"),
    "roe_3y": (True, "3-year ROE"), "roce_3y": (True, "3-year ROCE"), "sales_cagr_3y": (True, "3-year sales growth"),
    "profit_cagr_3y": (True, "3-year profit growth"), "fcf_yield": (True, "free-cash-flow yield"),
    "fii_change": (True, "FII buying"), "dii_change": (True, "DII buying"), "promoter_change": (True, "promoter buying"),
}

RUBRICS = {
    "LONG_TERM": {
        "quality": (30, ["roe", "roa", "opm", "npm", "roe_3y", "roce_3y"]),
        "growth": (30, ["sales_growth", "profit_growth", "sales_cagr_3y", "profit_cagr_3y"]),
        "valuation": (25, ["pe", "pb", "ev_ebitda", "earnings_yield", "fcf_yield"]),
        "balance_sheet": (10, ["debt_to_equity", "current_ratio"]),
        "ownership": (5, ["fii_change", "dii_change", "promoter_change"]),
    },
    "SWING": {
        "momentum": (60, ["ret_1m", "ret_3m", "ret_6m", "vs_dma50", "vs_dma200", "rsi_fit"]),
        "risk": (20, ["volatility", "max_drawdown"]),
        "valuation": (10, ["pe", "earnings_yield"]),
        "liquidity": (10, ["market_cap"]),
    },
}

# Lenders run on borrowed money: debt/equity and current ratio say little about them.
FINANCIAL_WORDS = ("bank", "financ", "nbfc", "insurance", "lending", "housing finance", "capital markets")


def _is_financial(row: dict) -> bool:
    txt = f"{row.get('sector') or ''} {row.get('industry') or ''}".lower()
    return any(w in txt for w in FINANCIAL_WORDS)


def _value(row: dict, m: str) -> float | None:
    if m == "rsi_fit":  # momentum without being overbought: best around 60
        r = row.get("rsi14")
        return None if r is None else -abs(float(r) - 60)
    v = row.get(m)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    if m in ("pe", "ev_ebitda") and v <= 0:
        return 1e9  # loss-making / negative EBITDA: the worst valuation, not the best
    if m in ("debt_to_equity", "current_ratio") and _is_financial(row):
        return None
    return v


def _percentiles(values: dict[str, float], higher_better: bool) -> dict[str, float]:
    """0-100 by rank among the stocks that have the figure; ties share the average rank."""
    if not values:
        return {}
    if len(values) == 1:
        return {k: 50.0 for k in values}
    items = sorted(values.items(), key=lambda kv: kv[1] if higher_better else -kv[1])
    out, i, n = {}, 0, len(items)
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        pct = ((i + j) / 2) / (n - 1) * 100
        for k in range(i, j + 1):
            out[items[k][0]] = round(pct, 1)
        i = j + 1
    return out


def score(rows: list[dict], horizon: str) -> dict[str, dict]:
    """{symbol: {score, verdict, breakdown{category: 0-100|None}, coverage, strengths, weaknesses}}."""
    rubric = RUBRICS.get(horizon, RUBRICS["LONG_TERM"])
    metrics = sorted({m for _, ms in rubric.values() for m in ms})
    pct = {m: _percentiles({r["symbol"]: v for r in rows if (v := _value(r, m)) is not None}, METRICS[m][0]) for m in metrics}
    out = {}
    for r in rows:
        sym = r["symbol"]
        total, known, used = 0.0, 0, 0
        breakdown = {}
        for cat, (w, ms) in rubric.items():
            vals = [pct[m][sym] for m in ms if sym in pct[m]]
            used += len(ms)
            known += len(vals)
            breakdown[cat] = round(sum(vals) / len(vals), 1) if vals else None
            total += w * (breakdown[cat] if vals else 50.0)
        coverage = known / used if used else 0
        s = total / sum(w for w, _ in rubric.values())
        if coverage < 0.6:
            s -= 5 * (0.6 - coverage) / 0.6 * 2  # up to -10 for a stock with almost no data
        s = round(max(0.0, min(100.0, s)), 1)
        ranked = sorted(((pct[m][sym], m) for m in metrics if sym in pct[m]), reverse=True)
        out[sym] = {"score": s, "verdict": verdict(s), "breakdown": breakdown, "coverage": round(coverage * 100),
                    "strengths": [METRICS[m][1] for p, m in ranked[:3] if p >= 60],
                    "weaknesses": [METRICS[m][1] for p, m in ranked[::-1][:3] if p <= 40]}
    return out


def verdict(s: float) -> str:
    return "strong" if s >= 70 else "good" if s >= 55 else "neutral" if s >= 40 else "weak"


def explain(sym: str, sc: dict) -> str:
    """A plain sentence for stocks the AI didn't write about."""
    parts = []
    if sc["strengths"]:
        parts.append("best on " + ", ".join(sc["strengths"]))
    if sc["weaknesses"]:
        parts.append("weakest on " + ", ".join(sc["weaknesses"]))
    if sc["coverage"] < 60:
        parts.append(f"only {sc['coverage']}% of the figures known")
    return ("Within this list: " + "; ".join(parts) + ".") if parts else "Middle of the list on most figures."
