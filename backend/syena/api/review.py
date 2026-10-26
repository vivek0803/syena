"""Daily missed-moves review: big movers vs what the news pipeline did."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from syena.api.deps import authenticated
from syena.db import get_db
from syena.news import review

router = APIRouter(prefix="/api/review", tags=["review"], dependencies=[Depends(authenticated)])


@router.get("/missed")
def missed(day: str | None = None, db: Session = Depends(get_db)):
    return review.get(db, day)


@router.post("/missed/run")
def run(db: Session = Depends(get_db)):
    try:
        return review.build(db)
    except ValueError as e:
        raise HTTPException(409, str(e))
