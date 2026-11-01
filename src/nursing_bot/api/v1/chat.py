import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.api.dependencies import get_db, get_pipeline_dep
from nursing_bot.api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationItem,
    ConversationHistoryResponse,
    MessageItem,
)
from nursing_bot.api.security import limiter
from nursing_bot.config import get_settings
from nursing_bot.generation.pipeline import GenerationPipeline
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import ChatMessage, ChatSession

router = APIRouter(tags=["Chat"])


async def get_or_create_session(
    db: AsyncSession, session_id: str | None = None
) -> ChatSession:
    sid = session_id or str(uuid.uuid4())
    stmt = select(ChatSession).where(ChatSession.id == sid)
    res = await db.execute(stmt)
    chat_session = res.scalar_one_or_none()
    if not chat_session:
        chat_session = ChatSession(id=sid)
        db.add(chat_session)
        await db.flush()
    return chat_session


async def load_session_history(db: AsyncSession, session_id: str) -> list[dict[str, str]]:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    res = await db.execute(stmt)
    messages = res.scalars().all()
    return [{"role": m.role, "content": m.content} for m in messages]


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(get_settings().CHAT_RATE_LIMIT)
async def chat_endpoint(
    request: Request,
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    pipeline: GenerationPipeline = Depends(get_pipeline_dep),
):
    """Chat endpoint supporting client-side sliding window, SSE streaming, and optional DB history."""
    settings = get_settings()
    request_filters = (
        req.filters.model_dump(exclude_none=True) if req.filters is not None else None
    )
    session_id = req.session_id or str(uuid.uuid4())

    # Resolve context window: Client-side ephemeral context window takes precedence
    if req.recent_context is not None:
        history = [
            {"role": t.role, "content": t.content}
            for t in req.recent_context[-settings.MAX_RECENT_CONTEXT_TURNS:]
        ]
    elif settings.ENABLE_DATABASE_CHAT_HISTORY:
        chat_session = await get_or_create_session(db, session_id)
        history = await load_session_history(db, chat_session.id)
    else:
        history = []

    if req.stream:
        async def event_generator() -> AsyncIterator[str]:
            full_answer: list[str] = []
            async with get_session() as stream_db:
                if settings.ENABLE_DATABASE_CHAT_HISTORY:
                    stream_session = await get_or_create_session(stream_db, session_id)
                    stream_db.add(
                        ChatMessage(
                            session_id=stream_session.id,
                            role="user",
                            content=req.query,
                        )
                    )
                    await stream_db.flush()

                async for evt in pipeline.astream_query(
                    session=stream_db,
                    query=req.query,
                    history=history,
                    filters=request_filters,
                    top_k=req.top_k,
                ):
                    if evt["type"] == "citations":
                        yield f"event: citations\ndata: {json.dumps(evt['citations'])}\n\n"
                    elif evt["type"] == "token":
                        full_answer.append(evt["content"])
                        yield f"event: token\ndata: {json.dumps(evt['content'])}\n\n"
                    elif evt["type"] == "end":
                        yield f"event: end\ndata: {json.dumps(evt)}\n\n"

                if settings.ENABLE_DATABASE_CHAT_HISTORY and full_answer:
                    stream_db.add(
                        ChatMessage(
                            session_id=session_id,
                            role="assistant",
                            content="".join(full_answer),
                        )
                    )
                    await stream_db.commit()

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Synchronous flow
    if settings.ENABLE_DATABASE_CHAT_HISTORY:
        chat_session = await get_or_create_session(db, session_id)
        db.add(ChatMessage(session_id=chat_session.id, role="user", content=req.query))
        await db.flush()

    response = await pipeline.aquery(
        session=db,
        query=req.query,
        history=history,
        filters=request_filters,
        top_k=req.top_k,
    )

    if settings.ENABLE_DATABASE_CHAT_HISTORY:
        db.add(ChatMessage(session_id=session_id, role="assistant", content=response.answer))
        await db.commit()

    return ChatResponse(
        answer=response.answer,
        citations=[CitationItem(**c) for c in response.citations],
        session_id=session_id,
        is_grounded=response.is_grounded,
        gate_passed=response.gate_passed,
        top_score=response.top_score,
        applied_filters=response.applied_filters,
        abstention_reason=response.abstention_reason,
    )


@router.get("/chat/sessions/{session_id}", response_model=ConversationHistoryResponse)
async def get_session_history(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    res = await db.execute(stmt)
    messages = res.scalars().all()
    if not messages:
        if not get_settings().ENABLE_DATABASE_CHAT_HISTORY:
            return ConversationHistoryResponse(
                session_id=session_id,
                messages=[],
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return ConversationHistoryResponse(
        session_id=session_id,
        messages=[
            MessageItem(role=m.role, content=m.content, created_at=m.created_at)
            for m in messages
        ],
    )
