from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.generation.pipeline import GenerationPipeline
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.retrieval.gate import RelevanceGate
from nursing_bot.retrieval.retriever import PostgresHybridRetriever
from nursing_bot.storage.database import get_session
from nursing_bot.storage.object_store import S3Backend

_storage_instance: S3Backend | None = None
_llm_client_instance: LiteLLMClient | None = None


def _resolve_secret(val: Any) -> str | None:
    if val is None:
        return None
    return val.get_secret_value() if hasattr(val, "get_secret_value") else str(val)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Provide an asynchronous database session."""
    async with get_session() as session:
        yield session


def get_storage_dep() -> S3Backend:
    """Provide the configured S3 / MinIO backend."""
    global _storage_instance
    if _storage_instance is None:
        settings = get_settings()
        _storage_instance = S3Backend(
            endpoint_url=settings.S3_ENDPOINT_URL,
            access_key_id=settings.S3_ACCESS_KEY_ID,
            secret_access_key=_resolve_secret(settings.S3_SECRET_ACCESS_KEY) or "",
            bucket_name=settings.S3_BUCKET_NAME,
            region=settings.S3_REGION,
        )
    return _storage_instance


def get_llm_client_dep() -> LiteLLMClient:
    """Provide the unified LiteLLM client."""
    global _llm_client_instance
    if _llm_client_instance is None:
        settings = get_settings()
        _llm_client_instance = LiteLLMClient(
            model=settings.LLM_MODEL,
            embedding_model=settings.EMBEDDING_MODEL,
            rerank_model=settings.RERANK_MODEL,
            api_key=_resolve_secret(settings.LLM_API_KEY),
            base_url=settings.LLM_BASE_URL,
            embedding_base_url=settings.EMBEDDING_BASE_URL,
            embedding_api_key=_resolve_secret(settings.EMBEDDING_API_KEY),
            embedding_dimensions=settings.EMBEDDING_DIMENSIONS,
        )
    return _llm_client_instance


def get_pipeline_dep(
    llm_client: LiteLLMClient = Depends(get_llm_client_dep),
) -> GenerationPipeline:
    """Build the generation pipeline with hybrid retriever and relevance gate."""
    settings = get_settings()
    retriever = PostgresHybridRetriever(
        session=None,
        llm_client=llm_client,
        top_k=settings.RETRIEVAL_TOP_K,
        rrf_k=60,
        rerank_enabled=settings.RERANK_ENABLED,
        rerank_top_n=settings.RERANK_TOP_K,
    )
    gate = RelevanceGate(rrf_threshold=settings.GATE_THRESHOLD_RRF)
    return GenerationPipeline(
        retriever=retriever,
        llm_client=llm_client,
        gate=gate,
        default_top_k=settings.RETRIEVAL_TOP_K,
    )
