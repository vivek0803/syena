"""Shared eval statistics: accuracy with Wilson CI, precision/recall, confusion matrix, calibration."""
import math
from collections import Counter

LABELS = ["BUY", "SELL", "HOLD"]


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def accuracy(pairs: list[tuple[str, str]]) -> dict:
    n = len(pairs)
    k = sum(1 for p, a in pairs if p == a)
    return {"value": round(k / n, 4) if n else None, "n": n, "ci95": wilson(k, n)}


def confusion(pairs: list[tuple[str, str]], labels=LABELS) -> dict:
    m = {a: {p: 0 for p in labels} for a in labels}
    for p, a in pairs:
        if a in m and p in m[a]:
            m[a][p] += 1
    return m  # m[actual][predicted]


def precision_recall(pairs: list[tuple[str, str]], label: str) -> dict:
    tp = sum(1 for p, a in pairs if p == label and a == label)
    fp = sum(1 for p, a in pairs if p == label and a != label)
    fn = sum(1 for p, a in pairs if p != label and a == label)
    return {"precision": round(tp / (tp + fp), 4) if tp + fp else None, "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "tp": tp, "fp": fp, "fn": fn}


def calibration(conf_hits: list[tuple[float, bool]], bins: int = 5) -> dict:
    """conf in 0-100. Returns per-bin predicted vs observed hit rate, and expected calibration error."""
    rows, n = [], len(conf_hits)
    for b in range(bins):
        lo, hi = 100 * b / bins, 100 * (b + 1) / bins
        sel = [(c, h) for c, h in conf_hits if lo <= c < hi or (b == bins - 1 and c == 100)]
        if not sel:
            rows.append({"bin": f"{lo:.0f}-{hi:.0f}", "n": 0, "predicted": None, "observed": None})
            continue
        pred = sum(c for c, _ in sel) / len(sel) / 100
        obs = sum(1 for _, h in sel if h) / len(sel)
        rows.append({"bin": f"{lo:.0f}-{hi:.0f}", "n": len(sel), "predicted": round(pred, 3), "observed": round(obs, 3),
                     "ci95": wilson(sum(1 for _, h in sel if h), len(sel))})
    ece = sum(r["n"] / n * abs(r["predicted"] - r["observed"]) for r in rows if r["n"]) if n else None
    return {"bins": rows, "ece": None if ece is None else round(ece, 4), "n": n}


def direction_label(ret_pct: float | None, threshold_pct: float = 0.5) -> str | None:
    """Actual outcome label from a forward return, net of a no-trade band covering costs."""
    if ret_pct is None:
        return None
    return "BUY" if ret_pct > threshold_pct else "SELL" if ret_pct < -threshold_pct else "HOLD"


def counts(xs) -> dict:
    return dict(Counter(xs))
