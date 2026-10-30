"""My notes: what the owner learned, for Syena's AI to keep in mind."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import notes
from syena.api.deps import authenticated
from syena.db import get_db
from syena.models import NoteUse, OwnerNote

router = APIRouter(prefix="/api/notes", tags=["notes"], dependencies=[Depends(authenticated)])
PURPOSES = {"news_analysis": "News verdict", "news_macro": "Macro mapping", "top_news": "Top 10 news", "ask_ai": "Syena AI",
            "stock_compare": "Compare", "manual_review": "Manual invest", "trade_review": "Trade review"}


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    title: str = Field(default="", max_length=200)
    kind: str = Field(default="general", pattern="^(macro|sector|stock|rule|general)$")
    pinned: bool = False
    applies: list[str] = Field(default_factory=lambda: list(notes.AREAS), max_length=10)


class PatchIn(BaseModel):
    active: bool | None = None
    pinned: bool | None = None


class TryIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    area: str = Field(default="news", pattern="^(news|ask|compare|invest|review)$")


def _get(db: Session, note_id: int) -> OwnerNote:
    n = db.get(OwnerNote, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    return n


@router.get("/meta")
def meta():
    return {"areas": notes.AREAS, "kinds": list(notes.KINDS), "purposes": PURPOSES}


@router.get("")
def list_notes(db: Session = Depends(get_db)):
    rows = db.scalars(select(OwnerNote).order_by(OwnerNote.pinned.desc(), OwnerNote.updated_at.desc())).all()
    return [notes.to_dict(n, notes.outcomes(db, n.id) if n.used_count else None) for n in rows]


@router.post("")
def create(body: NoteIn, db: Session = Depends(get_db)):
    try:
        n = notes.save(db, body=body.body, title=body.title, kind=body.kind, pinned=body.pinned, applies=body.applies)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return notes.to_dict(n)


@router.put("/{note_id}")
def update(note_id: int, body: NoteIn, db: Session = Depends(get_db)):
    try:
        n = notes.save(db, body=body.body, title=body.title, kind=body.kind, pinned=body.pinned, applies=body.applies,
                       note=_get(db, note_id))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return notes.to_dict(n)


@router.patch("/{note_id}")
def patch(note_id: int, body: PatchIn, db: Session = Depends(get_db)):
    from syena.db import utcnow

    n = _get(db, note_id)
    if body.active is not None:
        n.active = body.active
    if body.pinned is not None:
        n.pinned = body.pinned
    n.updated_at = utcnow()
    db.commit()
    return notes.to_dict(n)


@router.delete("/{note_id}")
def delete(note_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, note_id))
    db.commit()
    return {"deleted": True}


@router.post("/{note_id}/reindex")
def reindex(note_id: int, db: Session = Depends(get_db)):
    return notes.to_dict(notes.digest(db, _get(db, note_id)))


@router.get("/{note_id}/uses")
def uses(note_id: int, db: Session = Depends(get_db)):
    _get(db, note_id)
    rows = db.scalars(select(NoteUse).where(NoteUse.note_id == note_id).order_by(NoteUse.id.desc()).limit(50)).all()
    return {"outcomes": notes.outcomes(db, note_id),
            "uses": [{"ts": u.ts.isoformat(), "purpose": u.purpose, "label": PURPOSES.get(u.purpose, u.purpose), "ref": u.ref,
                      "influenced": u.influenced, "ai_call_id": u.ai_call_id} for u in rows]}


@router.post("/try")
def try_text(body: TryIn, db: Session = Depends(get_db)):
    """Which notes Syena would read for this headline or question (no AI cost)."""
    from syena.ask import find_symbols

    syms = find_symbols(db, body.text)
    found = notes.match(db, body.area, body.text, syms)
    return {"symbols": syms, "matches": [{"id": m["note"].id, "title": m["note"].title, "score": m["score"], "why": m["why"]} for m in found]}
