import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.api.dependencies import get_db, get_pipeline_dep
from nursing_bot.api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationItem,
    ConversationHistoryResponse,
)
from nursing_bot.api.security import limiter
from nursing_bot.config import get_settings
from nursing_bot.generation.pipeline import GenerationPipeline

router = APIRouter(tags=["Chat"])


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(get_settings().CHAT_RATE_LIMIT)
async def chat_endpoint(
    request: Request,
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    pipeline: GenerationPipeline = Depends(get_pipeline_dep),
):
    """Chat endpoint supporting client-side ephemeral sliding window context and true SSE streaming."""
    settings = get_settings()
    request_filters = (
        req.filters.model_dump(exclude_none=True) if req.filters is not None else None
    )
    session_id = req.session_id or str(uuid.uuid4())

    # Resolve context window: Client-side ephemeral sliding window
    if req.recent_context is not None:
        history = [
            {"role": t.role, "content": t.content}
            for t in req.recent_context[-settings.MAX_RECENT_CONTEXT_TURNS :]
        ]
    else:
        history = []

    if req.stream:

        async def event_generator() -> AsyncIterator[str]:
            async for evt in pipeline.astream_query(
                session=db,
                query=req.query,
                history=history,
                filters=request_filters,
                top_k=req.top_k,
            ):
                if evt["type"] == "citations":
                    yield f"event: citations\ndata: {json.dumps(evt['citations'])}\n\n"
                elif evt["type"] == "token":
                    yield f"event: token\ndata: {json.dumps(evt['content'])}\n\n"
                elif evt["type"] == "end":
                    yield f"event: end\ndata: {json.dumps(evt)}\n\n"

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
    response = await pipeline.aquery(
        session=db,
        query=req.query,
        history=history,
        filters=request_filters,
        top_k=req.top_k,
    )

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
):
    """Client-side ephemeral sliding window architecture maintains history in client session."""
    return ConversationHistoryResponse(
        session_id=session_id,
        messages=[],
    )
