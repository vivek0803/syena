"""Local, deterministic text embeddings for lesson / similar-trade retrieval.

Hashed unigrams + bigrams into 256 dims, L2-normalised. No external service, no data leaves the box.
Lessons are capped (settings.lessons.max_active), so cosine search in Python is fast; on Postgres the
vectors can be moved to pgvector later without changing callers.
"""
import hashlib
import math
import re

DIM = 256
_TOKEN = re.compile(r"[a-z0-9_]+")


def _idx(tok: str) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
    return h % DIM, 1.0 if (h >> 63) & 1 else -1.0


def embed(text: str) -> list[float]:
    toks = _TOKEN.findall((text or "").lower())
    v = [0.0] * DIM
    for t in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]:
        i, s = _idx(t)
        v[i] += s
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 6) for x in v]


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))
