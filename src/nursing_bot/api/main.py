"""FastAPI application: API routes, the web UI, security headers, lifecycle.

Run:  uvicorn nursing_bot.api.main:app
"""

import logging
import mimetypes
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import openai
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from nursing_bot import __version__
from nursing_bot.api.security import limiter
from nursing_bot.api.v1 import v1_router
from nursing_bot.config import get_settings
from nursing_bot.observability import (
    configure_logging,
    instrument_fastapi,
    setup_tracing,
)
from nursing_bot.storage.database import dispose_engine

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Windows can map .js to text/plain; browsers refuse module scripts served that way.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.validate_production()

    task_app: Any = None
    if settings.INGESTION_MODE == "queue":
        # The API only *defers* jobs; the worker container executes them.
        from nursing_bot.workers.tasks import app as procrastinate_app

        task_app = procrastinate_app
        await task_app.open_async()
    try:
        yield
    finally:
        if task_app is not None:
            await task_app.close_async()
        await dispose_engine()


def _content_security_policy(path: str) -> str | None:
    settings = get_settings()
    page_policy = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'"
    )
    if path.startswith("/ui/widget/"):
        # embed.js is loaded by the council's pages, so it must stay cross-origin readable.
        return f"{page_policy}; frame-ancestors {settings.widget_frame_ancestors}"
    if path.startswith("/ui/"):
        return f"{page_policy}; frame-ancestors 'none'"
    if path in ("/docs", "/redoc") or path.startswith("/docs/"):
        return (
            None  # Swagger UI loads its own assets; it is disabled in production anyway
        )
    return "default-src 'none'; frame-ancestors 'none'"


async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    path = request.url.path
    csp = _content_security_policy(path)
    if csp:
        response.headers["Content-Security-Policy"] = csp
    if not path.startswith("/ui/widget/"):
        response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


async def rate_limit_handler(request: Request, exc: Exception) -> Response:
    detail = getattr(exc, "detail", "")
    return JSONResponse(
        status_code=429,
        content={"detail": f"Too many requests ({detail}). Please wait a moment."},
    )


async def provider_error_handler(request: Request, exc: Exception) -> Response:
    """The AI provider is down, overloaded or rate-limiting: a clean 503, not a crash."""
    logger.warning(
        "AI provider error on %s: %s: %s", request.url.path, type(exc).__name__, exc
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": "The assistant is temporarily unavailable. Please try again shortly."
        },
        headers={"Retry-After": "30"},
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> Response:
    """Any other bug: log it with the traceback, answer in the documented JSON shape."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    is_production = settings.ENVIRONMENT == "production"

    app = FastAPI(
        title=f"{settings.KNOWLEDGE_AUTHORITY_NAME} API",
        description=f"Hybrid RAG platform for the {settings.KNOWLEDGE_BASE_NAME}",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    # LiteLLM's provider exceptions (connection, timeout, 5xx, 429 ...) all
    # derive from openai.APIError.
    app.add_exception_handler(openai.APIError, provider_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.middleware("http")(security_headers)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-Session-Token", "X-Admin-API-Key"],
        )

    app.include_router(v1_router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui/widget/chat.html")

    @app.get("/health", tags=["System"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/ui/admin", include_in_schema=False)
    async def admin_panel() -> RedirectResponse:
        # Relative redirect (so it stays correct behind the TLS proxy) to the
        # trailing-slash URL that the panel's relative asset paths need.
        return RedirectResponse("/ui/admin/", status_code=308)

    # html=True serves index.html for directory URLs such as /ui/admin/.
    app.mount("/ui", StaticFiles(directory=WEB_DIR, html=True), name="ui")

    setup_tracing("nursing-bot-api", settings)
    instrument_fastapi(app)
    return app


app = create_app()
