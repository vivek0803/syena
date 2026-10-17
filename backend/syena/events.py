"""Publish UI events (consumed by the SSE stream). Safe to call from API or worker."""
from typing import Any

from sqlalchemy.orm import Session

from syena.models import Event


def publish(db: Session, kind: str, payload: dict[str, Any] | None = None, commit: bool = True) -> None:
    db.add(Event(kind=kind, payload=payload or {}))
    if commit:
        db.commit()
