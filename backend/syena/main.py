"""FastAPI application factory."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from syena import __version__
from syena.api import admin, algos, auth, compare, health, intel, logs, mode, portfolio, screener, stock, trading
from syena.config import get_settings
from syena.db import SessionLocal
from syena.logging_setup import configure_logging
from syena.ai.llm import BudgetExceeded, LLMUnavailable
from syena.market.kite import KiteNotConnected
from syena.market.angel import AngelError
from syena.services import mode as mode_service
from syena.services.live_checks import register_live_checks

log = logging.getLogger("syena.app")

# Routes reachable without a session. Everything else must 401.
PUBLIC_PATHS = {"/health", "/api/auth/login"}
# Public but separately verified: one-time nonce (Kite redirect) / secret header + chat id (Telegram).
VERIFIED_PATHS = {"/kite/callback", "/api/telegram/webhook"}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def create_app(run_startup: bool = True) -> FastAPI:
    settings = get_settings()
    if run_startup:
        register_live_checks()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if run_startup:
            configure_logging(
                settings.log_level,
                to_db=settings.log_to_db,
                secrets=settings.secret_values(),
            )
            with SessionLocal() as db:
                mode_service.on_startup(db)
            log.info("syena started", extra={"version": __version__, "env": settings.env})
        yield

    is_dev = settings.env == "dev"
    app = FastAPI(
        title="Syena",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if is_dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if is_dev else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response

    app.include_router(health.router)
    app.include_router(auth.public_router)
    app.include_router(auth.router)
    app.include_router(mode.router)
    app.include_router(logs.router)
    app.include_router(trading.router)
    app.include_router(trading.public_router)
    app.include_router(algos.router)
    app.include_router(intel.router)
    app.include_router(portfolio.router)
    app.include_router(screener.router)
    app.include_router(compare.router)
    app.include_router(stock.router)
    app.include_router(admin.router)
    app.include_router(admin.public_router)

    @app.exception_handler(KiteNotConnected)
    async def _kite(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(AngelError)
    async def _angel(_, exc):
        logging.getLogger("syena.angel").warning("%s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(LLMUnavailable)
    async def _nollm(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(BudgetExceeded)
    async def _budget(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=429)

    @app.exception_handler(Exception)
    async def _unexpected(_, exc):
        name = type(exc).__name__
        if type(exc).__module__.startswith("kiteconnect"):
            # Zerodha rejected the call (permissions, expired session, bad input). Show its reason.
            logging.getLogger("syena.kite").warning("Zerodha error %s: %s", name, exc)
            return JSONResponse({"detail": f"Zerodha: {exc}"}, status_code=502)
        logging.getLogger("syena").exception("unhandled error")
        return JSONResponse({"detail": f"Unexpected error ({name}). Details are in the Syena logs."}, status_code=500)

    @app.exception_handler(ValueError)
    async def _value(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    return app


app = create_app()
