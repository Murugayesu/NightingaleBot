"""FastAPI dependency providers. Tests override these with `app.dependency_overrides`."""

from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.api.security import verify_session_token
from nursing_bot.config import get_settings
from nursing_bot.generation.pipeline import GenerationPipeline
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.retrieval.gate import RelevanceGate
from nursing_bot.retrieval.retriever import PostgresHybridRetriever
from nursing_bot.services import get_llm_client, get_storage
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import ChatSession
from nursing_bot.storage.object_store import S3Backend


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_session() as session:
        yield session


def get_storage_dep() -> S3Backend:
    return get_storage()


def get_llm_client_dep() -> LiteLLMClient:
    return get_llm_client()


def get_pipeline_dep(
    llm_client: LiteLLMClient = Depends(get_llm_client_dep),
) -> GenerationPipeline:
    s = get_settings()
    return GenerationPipeline(
        retriever=PostgresHybridRetriever(
            llm_client=llm_client,
            top_k=s.RETRIEVAL_TOP_K,
            rrf_k=s.RRF_K,
            rerank_enabled=s.RERANK_ENABLED,
            rerank_top_n=s.RERANK_TOP_K,
        ),
        llm_client=llm_client,
        gate=RelevanceGate(s.GATE_THRESHOLD_SIMILARITY, s.GATE_THRESHOLD_RERANK),
        default_top_k=s.RETRIEVAL_TOP_K,
    )


async def get_chat_session(
    x_session_token: str | None = Header(None, alias="X-Session-Token"),
    db: AsyncSession = Depends(get_db),
) -> ChatSession | None:
    """Resolve the caller's chat session from the X-Session-Token header.

    401 if the token is missing (when required), forged or expired.
    429 if the session has used up its token budget.
    """
    settings = get_settings()
    if not x_session_token:
        if settings.CHAT_REQUIRE_SESSION:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Missing X-Session-Token header."
            )
        return None

    session_id = verify_session_token(x_session_token)
    chat_session = await db.get(ChatSession, session_id) if session_id else None
    if chat_session is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired session token."
        )
    if chat_session.tokens_used >= settings.SESSION_TOKEN_BUDGET:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "This chat session has reached its usage limit. Please start a new chat later.",
        )
    return chat_session
