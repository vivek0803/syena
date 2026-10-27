"""Top 10 news of the day (built at 6 PM)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from syena.api.deps import authenticated
from syena.db import get_db
from syena.news import topnews

router = APIRouter(prefix="/api/top-news", tags=["top-news"], dependencies=[Depends(authenticated)])


@router.get("")
def get_top(day: str | None = None, db: Session = Depends(get_db)):
    return topnews.get(db, day)


@router.post("/run")
def run_now(db: Session = Depends(get_db)):
    """Build today's list now (it is also built automatically at 6 PM)."""
    from syena.ai import llm
    from syena.market import source

    try:
        md = source.get_market_data(db)
    except Exception:
        md = None
    try:
        return topnews.build(db, md=md, notify=False)
    except (llm.BudgetExceeded, llm.LLMUnavailable) as e:
        raise HTTPException(409, f"AI unavailable: {e}")
