from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from syena import __version__
from syena.db import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Public liveness/readiness probe. Deliberately reveals nothing about mode or positions."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok", "version": __version__}
    except Exception:
        return JSONResponse({"status": "degraded", "db": "error", "version": __version__}, status_code=503)
