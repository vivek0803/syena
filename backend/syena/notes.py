"""The owner's notes: things they learned that Syena's AI should keep in mind.

Write a note on the My notes page ("when crude oil falls, paint and tyre makers gain because their raw material gets
cheaper"). On save the triage model indexes it: a one-line summary, the cause-effect links, keywords and synonyms a
related headline would use, the affected NSE industries and stocks. When the AI later looks at news, a question, a
comparison, a manual investment or a closed trade, the notes that relate to it (same stocks or industries, matching
keywords, similar wording; plus notes marked "always") are added to what it reads, and it says which ones changed its
view. Every use is recorded so the page shows where a note was used and how the trades it touched turned out.

Notes inform the AI; they never bypass the trade gate, risk limits or PAPER/LIVE rules, which are code.
"""
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.ai import embeddings
from syena.db import utcnow
from syena.models import NoteUse, OwnerNote, Stock

log = logging.getLogger("syena.notes")

AREAS = {"news": "News trading", "ask": "Syena AI", "compare": "Compare stocks", "invest": "Manual invest", "review": "Trade reviews"}
KINDS = ("macro", "sector", "stock", "rule", "general")
PURPOSE_AREA = {"news_analysis": "news", "news_macro": "news", "top_news": "news", "ask_ai": "ask",
                "stock_compare": "compare", "manual_review": "invest", "trade_review": "review"}
STOP = set("""a an the and or but if then when while of to in on at by for with from as is are was were be been being it its
this that these those there their they them we our you your i me my so than too very can will would should could may might
do does did done have has had not no yes also just more most less much many some any all each into over under up down out
about after before because since until which who whom what where why how such only own same other off again further once""".split())
NOTE_RULE = ("\n\n<owner_notes>\nThe owner of this system wrote these notes about what they have learned. They are the "
             "owner's views and rules of thumb, not market data: weigh them alongside the data above; where the data "
             "clearly contradicts a note, follow the data and say so. A note never overrides risk limits. If a note "
             "changed your view, add \"notes_used\": [its N numbers] to your JSON.\n{body}\n</owner_notes>")


def _basic_keywords(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z][a-z0-9&-]{2,}", text.lower()) if w not in STOP]
    out: list[str] = []
    for w in words:
        if w not in out:
            out.append(w)
    return out[:25]


def _industries(db: Session) -> list[str]:
    from syena.news.macro import industries

    try:
        return industries(db)
    except Exception:
        return []


def digest(db: Session, note: OwnerNote) -> OwnerNote:
    """Index the note so it can be found later: AI digest when available, otherwise words and named stocks."""
    from syena.ai import llm
    from syena.ai.prompts import untrusted
    from syena.ask import find_symbols

    text = f"{note.title}\n{note.body}"
    note.embedding = embeddings.embed(text)
    inds = _industries(db)
    cfg = settings_store.get(db, "owner_notes")
    data = None
    if cfg.get("ai_digest", True) and llm.available():
        try:
            res = llm.call(db, "note_digest", "note_digest", tier="triage", max_tokens=700, temperature=0,
                           variables={"industries": ", ".join(inds) or "(not loaded)", "note": untrusted(text[:4000], "owner_note")})
            data = res.data or None
        except Exception as e:
            log.warning("note digest failed, indexing words only", extra={"note": note.id, "error": str(e)})
    syms = set(find_symbols(db, text))
    if data:
        named = [str(s).upper().strip() for s in data.get("symbols") or [] if s]
        if named:
            syms |= set(db.scalars(select(Stock.symbol).where(Stock.symbol.in_(named))))
        lower = {i.lower(): i for i in inds}
        note.sectors = [lower[i.lower()] for i in data.get("industries") or [] if isinstance(i, str) and i.lower() in lower][:12]
        kws = [str(k).lower().strip() for k in data.get("keywords") or [] if k and len(str(k)) <= 40]
        note.keywords = list(dict.fromkeys(kws))[:30] or _basic_keywords(text)
        note.summary = str(data.get("summary") or "")[:400] or None
        note.effects = [{"when": str(e.get("when", ""))[:200], "then": str(e.get("then", ""))[:200],
                         "direction": e.get("direction") if e.get("direction") in ("positive", "negative", "mixed") else "mixed"}
                        for e in (data.get("effects") or [])[:6] if isinstance(e, dict) and e.get("when")]
        if not note.title.strip() and data.get("title"):
            note.title = str(data["title"])[:200]
        if note.kind == "general" and data.get("kind") in KINDS:
            note.kind = data["kind"]
        note.digest_status = "ai"
    else:
        note.keywords, note.sectors, note.summary, note.effects = _basic_keywords(text), [], None, []
        note.digest_status = "basic"
    note.symbols = sorted(syms)[:20]
    if not note.title.strip():
        note.title = (note.body.strip().split("\n")[0])[:80] or "Note"
    db.commit()
    return note


def save(db: Session, *, body: str, title: str = "", kind: str = "general", pinned: bool = False,
         applies: list[str] | None = None, note: OwnerNote | None = None) -> OwnerNote:
    body = (body or "").strip()
    if len(body) < 5:
        raise ValueError("Write a little more: a sentence or two on what you learned.")
    if len(body) > 8000:
        raise ValueError("Keep a note under 8000 characters; split long ideas into separate notes.")
    applies = [a for a in (applies or list(AREAS)) if a in AREAS] or list(AREAS)
    new = note is None
    if new:
        note = OwnerNote(title="", body="")
        db.add(note)
    changed = new or note.body != body or note.title != (title or "").strip()
    note.title, note.body = (title or "").strip()[:200], body
    note.kind = kind if kind in KINDS else "general"
    note.pinned, note.applies, note.updated_at = bool(pinned), applies, utcnow()
    db.commit()
    return digest(db, note) if changed else note


def _sectors_of(db: Session, symbols: list[str]) -> set[str]:
    if not symbols:
        return set()
    return {i for i in db.scalars(select(Stock.industry).where(Stock.symbol.in_(symbols))) if i} | \
           {s for s in db.scalars(select(Stock.sector).where(Stock.symbol.in_(symbols))) if s}


def match(db: Session, area: str, query: str, symbols: list[str] | None = None, limit: int | None = None) -> list[dict]:
    """Notes relevant to this text and these stocks, best first: [{note, score, why}]."""
    cfg = settings_store.get(db, "owner_notes")
    if not cfg.get("enabled", True):
        return []
    notes = [n for n in db.scalars(select(OwnerNote).where(OwnerNote.active.is_(True))) if area in (n.applies or AREAS)]
    if not notes:
        return []
    q = (query or "").lower()
    syms = {s.upper() for s in symbols or []}
    secs = {s.lower() for s in _sectors_of(db, list(syms))}
    qv = embeddings.embed(query or "")
    out = []
    for n in notes:
        why, score = [], 0.0
        hit_s = syms & set(n.symbols or [])
        if hit_s:
            score += 3; why.append("about " + ", ".join(sorted(hit_s)))
        hit_i = secs & {s.lower() for s in n.sectors or []}
        if hit_i:
            score += 2; why.append("same industry")
        kws = [k for k in n.keywords or [] if len(k) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", q)]
        if kws:
            score += min(len(kws), 3); why.append("mentions " + ", ".join(kws[:3]))
        sim = embeddings.cosine(qv, n.embedding)
        if sim >= float(cfg.get("min_similarity", 0.3)):
            score += sim * 2; why.append("similar wording")
        if n.pinned:
            score += 0.5; why.append("always considered")
        # one generic keyword alone is weak; a stock, industry, two keywords, similar wording or "always" qualifies
        if n.pinned or hit_s or hit_i or len(kws) >= 2 or (kws and sim >= 0.15) or sim >= float(cfg.get("min_similarity", 0.3)):
            out.append({"note": n, "score": round(score, 2), "why": "; ".join(why)})
    out.sort(key=lambda x: -x["score"])
    return out[: limit or int(cfg.get("max_notes", 6))]


def block(db: Session, area: str, query: str, symbols: list[str] | None = None) -> tuple[str, list[OwnerNote]]:
    """Text to add to an AI prompt, and the notes in it."""
    picked = match(db, area, query, symbols)
    if not picked:
        return "", []
    cap = int(settings_store.get(db, "owner_notes").get("max_chars", 3500))
    parts, used, size = [], [], 0
    for m in picked:
        n = m["note"]
        txt = f"N{n.id} ({n.kind}, {m['why']}): {n.title}\n{n.body.strip()}"
        if size + len(txt) > cap:
            txt = txt[: max(0, cap - size)]
            if len(txt) < 120:
                break
        parts.append(txt); used.append(n); size += len(txt)
    body = "\n\n".join(parts).replace("</owner_notes", "&lt;/owner_notes")
    return NOTE_RULE.format(body=body), used


def record(db: Session, used: list[OwnerNote], purpose: str, ai_call_id: int | None, data: dict | None, ref: str | None) -> list[int]:
    """Log that these notes were shown to the AI; returns the ids it said changed its view."""
    if not used:
        return []
    said = set()
    for x in (data or {}).get("notes_used") or [] if isinstance(data, dict) else []:
        m = re.fullmatch(r"N?(\d+)", str(x).strip().upper())
        if m:
            said.add(int(m.group(1)))
    now = utcnow()
    for n in used:
        n.used_count = (n.used_count or 0) + 1
        n.last_used_at = now
        db.add(NoteUse(note_id=n.id, ai_call_id=ai_call_id, purpose=purpose, ref=(ref or "")[:200] or None,
                       influenced=n.id in said, ts=now))
    db.commit()
    return sorted(said & {n.id for n in used})


def keyword_hit(db: Session, text: str) -> bool:
    """Does any active news note's keyword appear in this text? Lets the macro mapper look at items about themes
    the owner cares about even when they miss Syena's built-in macro word list."""
    q = (text or "").lower()
    for kws, applies in db.execute(select(OwnerNote.keywords, OwnerNote.applies).where(OwnerNote.active.is_(True))):
        if "news" not in (applies or AREAS):
            continue
        hits = [k for k in kws or [] if len(k) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", q)]
        if len(hits) >= 2:
            return True
    return False


def outcomes(db: Session, note_id: int) -> dict:
    """How the news trades whose analysis read this note turned out."""
    from syena.models import NewsAnalysis, Trade, TradeStatus

    call_ids = [c for c in db.scalars(select(NoteUse.ai_call_id).where(NoteUse.note_id == note_id, NoteUse.purpose == "news_analysis",
                                                                         NoteUse.ai_call_id.is_not(None)).order_by(NoteUse.id.desc()).limit(500))]
    if not call_ids:
        return {"trades": 0, "closed": 0, "wins": 0, "pnl": 0.0}
    items = set(db.scalars(select(NewsAnalysis.news_item_id).where(NewsAnalysis.ai_call_id.in_(call_ids))))
    trades = [t for t in db.scalars(select(Trade).where(Trade.source == "news").order_by(Trade.id.desc()).limit(3000))
              if items & set(t.news_item_ids or [])]
    closed = [t for t in trades if t.status == TradeStatus.CLOSED and t.profit_loss is not None]
    return {"trades": len(trades), "closed": len(closed), "wins": sum(1 for t in closed if t.profit_loss > 0),
            "pnl": round(sum(t.profit_loss for t in closed), 2)}


def to_dict(n: OwnerNote, with_outcomes: dict | None = None) -> dict:
    return {"id": n.id, "title": n.title, "body": n.body, "kind": n.kind, "pinned": n.pinned, "active": n.active,
            "applies": n.applies or list(AREAS), "summary": n.summary, "effects": n.effects or [], "keywords": n.keywords or [],
            "sectors": n.sectors or [], "symbols": n.symbols or [], "digest_status": n.digest_status,
            "used_count": n.used_count or 0, "last_used_at": n.last_used_at.isoformat() if n.last_used_at else None,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "updated_at": n.updated_at.isoformat() if n.updated_at else None, "outcomes": with_outcomes}
