from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.api.dependencies import get_db, get_llm_client_dep, get_storage_dep
from nursing_bot.api.schemas import HealthResponse, SystemStatsResponse
from nursing_bot.config import get_settings
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.storage.models import ChatSession, Chunk, Document
from nursing_bot.storage.object_store import S3Backend

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/health", response_model=HealthResponse)
async def admin_health(
    db: AsyncSession = Depends(get_db),
    storage: S3Backend = Depends(get_storage_dep),
    llm_client: LiteLLMClient = Depends(get_llm_client_dep),
):
    settings = get_settings()

    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_status = f"unhealthy: {exc}"

    storage_status = "ok"
    try:
        await storage.list_keys(limit=1)
    except Exception as exc:  # noqa: BLE001
        storage_status = f"unhealthy: {exc}"

    return HealthResponse(
        status="ok" if db_status == "ok" and storage_status == "ok" else "degraded",
        environment=settings.ENVIRONMENT,
        database=db_status,
        storage=storage_status,
        llm_model=settings.LLM_MODEL,
        embedding_model=settings.EMBEDDING_MODEL,
    )


@router.get("/stats", response_model=SystemStatsResponse)
async def admin_stats(
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()

    docs_count = (await db.execute(select(func.count(Document.id)))).scalar_one()
    chunks_count = (await db.execute(select(func.count(Chunk.id)))).scalar_one()
    sessions_count = (await db.execute(select(func.count(ChatSession.id)))).scalar_one()

    return SystemStatsResponse(
        total_documents=docs_count,
        total_chunks=chunks_count,
        total_sessions=sessions_count,
        active_embedding_model=settings.EMBEDDING_MODEL,
    )
