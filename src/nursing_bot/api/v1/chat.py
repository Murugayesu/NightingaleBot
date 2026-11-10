"""Public chat endpoints used by the website widget (see docs/api-contract.md)."""

import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot import privacy
from nursing_bot.api.dependencies import get_chat_session, get_db, get_pipeline_dep
from nursing_bot.api.schemas import (
    ChatRequest,
    ChatResponse,
    ChatSessionCreated,
    CitationItem,
    ConversationHistoryResponse,
    MessageItem,
    WidgetConfigResponse,
)
from nursing_bot.api.security import (
    issue_session_token,
    limiter,
    new_session_id,
    verify_session_token,
)
from nursing_bot.config import get_settings
from nursing_bot.generation.guardrails import MAX_QUERY_LENGTH
from nursing_bot.generation.pipeline import GenerationPipeline, RAGResponse, UsageMeter
from nursing_bot.retrieval.metadata import InvalidFilterError, validate_filters
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import ChatMessage, ChatSession

router = APIRouter(tags=["Chat"])
logger = logging.getLogger(__name__)


@router.get("/widget/config", response_model=WidgetConfigResponse)
async def widget_config() -> WidgetConfigResponse:
    s = get_settings()
    return WidgetConfigResponse(
        knowledge_base_name=s.KNOWLEDGE_BASE_NAME,
        authority_name=s.KNOWLEDGE_AUTHORITY_NAME,
        contact_url=s.KNOWLEDGE_CONTACT_URL,
        welcome_message=s.WIDGET_WELCOME_MESSAGE,
        require_session=s.CHAT_REQUIRE_SESSION,
        max_query_chars=MAX_QUERY_LENGTH,
        max_context_turns=s.MAX_RECENT_CONTEXT_TURNS,
    )


@router.post(
    "/chat/session",
    response_model=ChatSessionCreated,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(lambda: get_settings().SESSION_CREATE_RATE_LIMIT)
async def create_chat_session(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ChatSessionCreated:
    s = get_settings()
    session_id = new_session_id()
    expires_epoch = int(time.time()) + s.SESSION_TTL_SECONDS
    expires_at = datetime.fromtimestamp(expires_epoch, UTC)
    db.add(
        ChatSession(
            id=session_id, expires_at=expires_at, tokens_used=0, metadata_json={}
        )
    )
    await db.commit()
    return ChatSessionCreated(
        session_id=session_id,
        session_token=issue_session_token(session_id, expires_epoch),
        expires_at=expires_at,
        token_budget=s.SESSION_TOKEN_BUDGET,
    )


async def _charge(db: AsyncSession, session_id: str, tokens: int) -> int:
    """Add tokens to the session atomically in SQL; returns the new total.

    `tokens_used = tokens_used + n` in one statement, so concurrent requests
    of the same session cannot overwrite each other's charges.
    """
    result = await db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(tokens_used=ChatSession.tokens_used + max(0, tokens))
        .returning(ChatSession.tokens_used)
    )
    return int(result.scalar_one())


async def _record_exchange(
    db: AsyncSession,
    chat_session: ChatSession | None,
    query: str,
    response: RAGResponse,
) -> int | None:
    """Charge tokens to the session and (optionally) store the redacted exchange.

    Returns the tokens remaining in the session budget, or None without a session.
    """
    if chat_session is None:
        return None
    settings = get_settings()
    total = await _charge(db, chat_session.id, response.tokens_used)
    if settings.ENABLE_DATABASE_CHAT_HISTORY:
        db.add(
            ChatMessage(
                session_id=chat_session.id, role="user", content=privacy.redact(query)
            )
        )
        db.add(
            ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                content=response.answer,
                citations_json=response.citations,
            )
        )
    await db.commit()
    return max(0, settings.SESSION_TOKEN_BUDGET - total)


async def _charge_unfinished(session_id: str, tokens: int) -> None:
    """Charge a stream that ended early (client disconnect, Stop button, error).

    Uses its own DB session (the request's may be mid-cancellation) and is
    shielded from cancellation, so an aborted stream can't escape the budget.
    """
    with anyio.CancelScope(shield=True):
        try:
            async with get_session() as db:
                await _charge(db, session_id, tokens)
                await db.commit()
        except Exception:
            logger.exception(
                "Could not charge %d tokens to session %s", tokens, session_id
            )


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(lambda: get_settings().CHAT_RATE_LIMIT)
async def chat_endpoint(
    request: Request,
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    chat_session: ChatSession | None = Depends(get_chat_session),
    pipeline: GenerationPipeline = Depends(get_pipeline_dep),
) -> Any:
    settings = get_settings()
    filters = req.filters.model_dump(exclude_none=True) if req.filters else None
    try:
        validate_filters(filters)
    except InvalidFilterError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    max_messages = max(0, settings.MAX_RECENT_CONTEXT_TURNS) * 2
    # Note `list[-0:]` is the WHOLE list, so 0 (context off) needs its own branch.
    turns = [t.model_dump() for t in (req.recent_context or [])]
    history = turns[-max_messages:] if max_messages else []
    session_id = chat_session.id if chat_session else None
    meter = UsageMeter()

    if not req.stream:
        response = await pipeline.aquery(
            db, req.query, history, filters, req.top_k, meter=meter
        )
        remaining = await _record_exchange(db, chat_session, req.query, response)
        return ChatResponse(
            answer=response.answer,
            citations=[CitationItem(**c) for c in response.citations],
            session_id=session_id,
            is_grounded=response.is_grounded,
            gate_passed=response.gate_passed,
            top_score=response.top_score,
            applied_filters=response.applied_filters,
            abstention_reason=response.abstention_reason,
            tokens_used=response.tokens_used,
            tokens_remaining=remaining,
        )

    async def events() -> AsyncIterator[str]:
        charged = False
        try:
            async for event in pipeline.astream_query(
                db, req.query, history, filters, req.top_k, meter=meter
            ):
                kind = event["type"]
                if kind == "citations":
                    yield _sse("citations", event["citations"])
                elif kind == "token":
                    yield _sse("token", event["content"])
                elif kind == "end":
                    final = RAGResponse(
                        answer=event["final_answer"],
                        citations=event["citations"],
                        tokens_used=event["tokens_used"],
                    )
                    event["tokens_remaining"] = await _record_exchange(
                        db, chat_session, req.query, final
                    )
                    charged = True
                    event.pop("type")
                    yield _sse("end", event)
        except Exception:
            logger.exception("Streaming chat failed")
            yield _sse(
                "error",
                {
                    "detail": "The assistant is temporarily unavailable. Please try again."
                },
            )
        finally:
            # Runs on success, error AND client disconnect (cancellation).
            if not charged and session_id is not None and meter.tokens > 0:
                await _charge_unfinished(session_id, meter.tokens)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/sessions/{session_id}", response_model=ConversationHistoryResponse)
async def get_session_history(
    session_id: str,
    x_session_token: str | None = Header(None, alias="X-Session-Token"),
    db: AsyncSession = Depends(get_db),
) -> ConversationHistoryResponse:
    if not x_session_token or verify_session_token(x_session_token) != session_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired session token."
        )
    rows = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at, ChatMessage.role.desc())
        )
    ).scalars()
    return ConversationHistoryResponse(
        session_id=session_id, messages=[MessageItem.model_validate(m) for m in rows]
    )
