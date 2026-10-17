"""FastAPI application factory."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from syena import __version__
from syena.api import auth, health, logs, mode
from syena.config import get_settings
from syena.db import SessionLocal
from syena.logging_setup import configure_logging
from syena.services import mode as mode_service

log = logging.getLogger("syena.app")

# Routes reachable without a session. Everything else must 401.
PUBLIC_PATHS = {"/health", "/api/auth/login"}

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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if run_startup:
            configure_logging(
                settings.log_level,
                to_db=settings.log_to_db,
                secrets=[settings.encryption_key.get_secret_value()],
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
    return app


app = create_app()
