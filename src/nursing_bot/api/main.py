"""FastAPI application: API routes, security headers, error handling, lifecycle.

Run:  uvicorn nursing_bot.api.main:app

The web app (frontend/) is static files served by Caddy; this process only
answers /api/* and /health.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import openai
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from nursing_bot import __version__
from nursing_bot.accounts.errors import AccountError, AccountLockedError
from nursing_bot.api.auth import is_admin_request
from nursing_bot.api.security import limiter
from nursing_bot.api.v1 import v1_router
from nursing_bot.config import get_settings
from nursing_bot.observability import (
    configure_logging,
    instrument_fastapi,
    setup_tracing,
)
from nursing_bot.storage.database import dispose_engine
from nursing_bot.usage import UsageLimitError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.validate_production()
    if settings.ENVIRONMENT == "production" and not settings.AUTH_COOKIE_SECURE:
        logger.warning(
            "AUTH_COOKIE_SECURE is false: sign-in cookies are sent over plain HTTP. "
            "Fine on localhost or a trusted network; set it to true once HTTPS is on."
        )

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
    if path in ("/docs", "/redoc") or path.startswith("/docs/"):
        # Swagger UI loads its own assets; it is disabled in production anyway.
        return None
    # Everything here is JSON or SSE: nothing may be rendered or framed.
    return "default-src 'none'; frame-ancestors 'none'"


UPLOAD_PATH = "/api/v1/documents/upload"
MAX_JSON_BODY_BYTES = 1024 * 1024  # chat and admin requests are a few KB
MULTIPART_OVERHEAD_BYTES = 1024 * 1024  # form fields and boundaries around the file


def _reject(status_code: int, detail: str) -> Response:
    return JSONResponse(status_code=status_code, content={"detail": detail})


async def request_guard(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Reject bad requests BEFORE anything reads their body.

    FastAPI parses a multipart body (spooling files to disk) before it runs
    the endpoint's auth dependency. Without this guard an anonymous client
    could stream endless gigabytes to the upload URL and fill the disk
    before ever receiving its 401.
    """
    if request.method in ("POST", "PUT", "PATCH"):
        is_upload = request.url.path == UPLOAD_PATH
        if "chunked" in request.headers.get("transfer-encoding", "").lower():
            return _reject(411, "Requests must declare Content-Length.")
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                return _reject(400, "Invalid Content-Length header.")
            limit = (
                get_settings().MAX_UPLOAD_SIZE_BYTES + MULTIPART_OVERHEAD_BYTES
                if is_upload
                else MAX_JSON_BODY_BYTES
            )
            if size > limit:
                return _reject(413, f"Request body exceeds the {limit}-byte limit.")
        if is_upload and not await is_admin_request(request):
            return _reject(
                401, "Sign in as an admin, or send a valid X-Admin-API-Key header."
            )
    return await call_next(request)


async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    path = request.url.path
    csp = _content_security_policy(path)
    if csp:
        response.headers["Content-Security-Policy"] = csp
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


async def account_error_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, AccountError)
    headers = (
        {"Retry-After": str(exc.retry_after_seconds)}
        if isinstance(exc, AccountLockedError)
        else None
    )
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.message}, headers=headers
    )


async def usage_limit_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, UsageLimitError)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
        headers={"Retry-After": "3600"},
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
    app.add_exception_handler(AccountError, account_error_handler)
    app.add_exception_handler(UsageLimitError, usage_limit_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    # Registered first = runs inside security_headers, so its rejections
    # also carry the security headers.
    app.middleware("http")(request_guard)
    app.middleware("http")(security_headers)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            # Cookies never cross origins: signed-in features are same-origin only.
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-Session-Token", "X-Admin-API-Key"],
        )

    app.include_router(v1_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": f"{settings.KNOWLEDGE_BASE_NAME} API", "health": "/health"}

    @app.get("/health", tags=["System"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    setup_tracing("nursing-bot-api", settings)
    instrument_fastapi(app)
    return app


app = create_app()
