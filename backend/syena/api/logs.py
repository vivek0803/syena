from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena.api.deps import authenticated
from syena.db import get_db
from syena.models import SystemLog

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(authenticated)])


class LogRow(BaseModel):
    id: int
    ts: datetime
    level: str
    module: str
    message: str
    stock: str | None
    algo_id: int | None
    extra: dict | None


@router.get("", response_model=list[LogRow])
def search_logs(
    level: str | None = None,
    module: str | None = None,
    stock: str | None = None,
    algo_id: int | None = None,
    q: str | None = Query(default=None, max_length=200),
    before_id: int | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(SystemLog)
    if level:
        stmt = stmt.where(SystemLog.level == level.upper())
    if module:
        stmt = stmt.where(SystemLog.module.startswith(module))
    if stock:
        stmt = stmt.where(SystemLog.stock == stock.upper())
    if algo_id is not None:
        stmt = stmt.where(SystemLog.algo_id == algo_id)
    if q:
        stmt = stmt.where(SystemLog.message.ilike(f"%{q}%"))
    if before_id:
        stmt = stmt.where(SystemLog.id < before_id)
    rows = db.scalars(stmt.order_by(SystemLog.id.desc()).limit(limit))
    return [LogRow.model_validate(r, from_attributes=True) for r in rows]
