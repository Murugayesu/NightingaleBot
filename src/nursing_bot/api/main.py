import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from nursing_bot import __version__
from nursing_bot.api.security import limiter
from nursing_bot.api.v1 import admin_router, chat_router, documents_router, v1_router
from nursing_bot.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    task_app = None
    if settings.INGESTION_MODE == "queue":
        try:
            from nursing_bot.workers.tasks import app as task_app
            await task_app.open_async()
        except Exception:  # noqa: BLE001
            logger.warning("Could not initialize Procrastinate background queue during lifespan.")
    try:
        yield
    finally:
        if task_app is not None:
            try:
                await task_app.close_async()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing task app: %s", exc)


settings = get_settings()

app = FastAPI(
    title=f"{settings.KNOWLEDGE_AUTHORITY_NAME} API",
    description=f"Enterprise Hybrid RAG Platform for {settings.KNOWLEDGE_BASE_NAME}",
    version=__version__,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

origins = [origin.strip() for origin in settings.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router)

app.include_router(chat_router)
app.include_router(documents_router)
app.include_router(admin_router)


@app.get("/", tags=["System"])
async def root():
    return {
        "status": "online",
        "name": settings.KNOWLEDGE_BASE_NAME,
        "authority": settings.KNOWLEDGE_AUTHORITY_NAME,
        "version": __version__,
    }


@app.get("/health", tags=["System"])
async def health():
    return {
        "status": "ok",
        "version": __version__,
    }
