"""Ask AI: stock-market questions answered with Syena's data."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from syena import ask
from syena.ai import llm
from syena.api.deps import authenticated
from syena.db import get_db

router = APIRouter(prefix="/api/ask", tags=["ask"], dependencies=[Depends(authenticated)])


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=6000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


@router.post("")
def ask_ai(body: AskIn, db: Session = Depends(get_db)):
    from syena.market import source

    try:
        md = source.get_market_data(db)
    except Exception:
        md = None  # answer from stored data only
    try:
        return ask.answer(db, body.question, [t.model_dump() for t in body.history], md)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except llm.BudgetExceeded as e:
        raise HTTPException(429, f"{e}: raise it in Settings → AI budget, or try tomorrow")
    except llm.LLMUnavailable as e:
        raise HTTPException(503, str(e))
