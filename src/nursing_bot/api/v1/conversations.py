"""Signed-in users' conversations (see docs/api-contract.md).

Asking a question (`POST /conversations/{id}/messages`) stores the question,
loads the recent turns and the rolling summary from the database, streams
the answer as SSE, then stores it with its answer mode and citations. If the
user presses Stop or the connection drops, the partial answer is stored as
`stopped` and its tokens still count towards the daily quota.
"""

import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, exists, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from nursing_bot.accounts.sessions import SignedInUser
from nursing_bot.api.auth import require_csrf, require_user, session_rate_key
from nursing_bot.api.dependencies import get_db, get_pipeline_dep
from nursing_bot.api.schemas import (
    CitationItem,
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationSummary,
    ConversationUpdate,
    FeedbackRequest,
    ImportRequest,
    ImportResult,
    SendMessageRequest,
    SendMessageResponse,
    StoredMessage,
)
from nursing_bot.api.security import limiter
from nursing_bot.config import get_settings
from nursing_bot.generation.memory import fallback_title, generate_title, update_summary
from nursing_bot.generation.pipeline import (
    GenerationPipeline,
    RAGResponse,
    UsageMeter,
    answer_label,
)
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.retrieval.metadata import InvalidFilterError, validate_filters
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import (
    AnswerMode,
    Conversation,
    Message,
    MessageFeedback,
    MessageStatus,
)
from nursing_bot.usage import check_allowance, record_usage

router = APIRouter(tags=["Conversations"])
logger = logging.getLogger(__name__)

DEFAULT_TITLE = "New chat"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """Client timestamps without a zone are taken as UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


# ------------------------------------------------------------ conversions
def _summary(c: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=c.id,
        title=c.title,
        pinned=c.pinned,
        archived=c.archived_at is not None,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _stored(m: Message, feedback: int | None = None) -> StoredMessage:
    return StoredMessage(
        id=m.id,
        role=m.role,
        content=m.content,
        answer_mode=m.answer_mode,
        label=answer_label(m.answer_mode) if m.answer_mode else None,
        citations=[CitationItem(**c) for c in (m.citations_json or [])],
        status=m.status,
        is_imported=m.is_imported,
        tokens_used=m.tokens_used,
        created_at=m.created_at,
        feedback=feedback,
    )


def _encode_cursor(c: Conversation) -> str:
    raw = json.dumps([c.pinned, c.updated_at.isoformat(), str(c.id)])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[bool, datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        pinned, updated, ident = json.loads(base64.urlsafe_b64decode(padded))
        return bool(pinned), datetime.fromisoformat(updated), uuid.UUID(ident)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid cursor."
        ) from exc


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --------------------------------------------------------------- queries
async def _owned_conversation(
    db: AsyncSession, user: SignedInUser, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        # Same answer for "not yours" and "does not exist": ids are not probeable.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return conversation


async def _messages(db: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    rows = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(rows.scalars())


def _history_before(messages: list[Message], message: Message) -> list[dict[str, str]]:
    """Complete turns before `message`, oldest first, for the condenser."""
    turns = []
    for m in messages:
        if m.id == message.id:
            break
        if m.status == MessageStatus.COMPLETE and m.content.strip():
            turns.append({"role": m.role, "content": m.content})
    return turns


async def _touch(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=func.now())
    )


# ------------------------------------------------------------------ list
@router.get("/conversations", response_model=ConversationList)
async def list_conversations(
    query: str | None = Query(None, max_length=200),
    archived: bool = Query(False),
    cursor: str | None = Query(None, max_length=200),
    limit: int = Query(30, ge=1, le=100),
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationList:
    """Pinned first, then most recently active. `query` searches titles and messages."""
    statement = select(Conversation).where(Conversation.user_id == user.id)
    statement = statement.where(
        Conversation.archived_at.is_not(None)
        if archived
        else Conversation.archived_at.is_(None)
    )
    if query and query.strip():
        pattern = _like(query.strip())
        statement = statement.where(
            or_(
                Conversation.title.ilike(pattern, escape="\\"),
                exists(
                    select(Message.id).where(
                        Message.conversation_id == Conversation.id,
                        Message.content.ilike(pattern, escape="\\"),
                    )
                ),
            )
        )
    if cursor:
        pinned, updated_at, ident = _decode_cursor(cursor)
        statement = statement.where(
            tuple_(Conversation.pinned, Conversation.updated_at, Conversation.id)
            < tuple_(literal(pinned), literal(updated_at), literal(ident))
        )
    statement = statement.order_by(
        Conversation.pinned.desc(),
        Conversation.updated_at.desc(),
        Conversation.id.desc(),
    ).limit(limit + 1)
    rows = list((await db.execute(statement)).scalars())
    page = rows[:limit]
    return ConversationList(
        conversations=[_summary(c) for c in page],
        next_cursor=_encode_cursor(page[-1]) if len(rows) > limit else None,
    )


@router.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_conversation(
    body: ConversationCreate,
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    conversation = Conversation(
        id=uuid.uuid4(),
        user_id=user.id,
        title=(body.title or "").strip() or DEFAULT_TITLE,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return _summary(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    conversation = await _owned_conversation(db, user, conversation_id)
    messages = await _messages(db, conversation.id)
    rated = await db.execute(
        select(MessageFeedback.message_id, MessageFeedback.rating).where(
            MessageFeedback.user_id == user.id,
            MessageFeedback.message_id.in_([m.id for m in messages]),
        )
    )
    ratings: dict[uuid.UUID, int] = {row[0]: row[1] for row in rated.all()}
    return ConversationDetail(
        **_summary(conversation).model_dump(),
        messages=[_stored(m, ratings.get(m.id)) for m in messages],
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummary,
    dependencies=[Depends(require_csrf)],
)
async def update_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    conversation = await _owned_conversation(db, user, conversation_id)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Title cannot be empty."
            )
        conversation.title = title
    if body.pinned is not None:
        conversation.pinned = body.pinned
    if body.archived is not None:
        conversation.archived_at = (
            (conversation.archived_at or _now()) if body.archived else None
        )
    await db.commit()
    await db.refresh(conversation)
    return _summary(conversation)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def delete_conversation(
    conversation_id: uuid.UUID,
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    conversation = await _owned_conversation(db, user, conversation_id)
    await db.delete(conversation)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- answering
def _sse(event: str, data: Any) -> str:
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


async def _save_answer(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    content: str,
    answer_mode: str | None,
    citations: list[dict[str, Any]],
    tokens: int,
    message_status: str,
) -> tuple[Message | None, int | None]:
    """Store the assistant turn (if it has text) and charge its tokens. Commits."""
    message = None
    if content.strip():
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            answer_mode=answer_mode,
            citations_json=citations,
            tokens_used=max(0, tokens),
            status=message_status,
            created_at=_now(),
        )
        db.add(message)
        await _touch(db, conversation_id)
    used_today = await record_usage(db, tokens, user_id)
    await db.commit()
    return message, used_today


def _quota_remaining(used_today: int | None) -> int | None:
    quota = get_settings().USER_DAILY_TOKEN_QUOTA
    if quota <= 0 or used_today is None:
        return None
    return max(0, quota - used_today)


async def _save_unfinished(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    answer_mode: str | None,
    tokens: int,
    message_status: str,
) -> None:
    """Store an answer that did not finish (Stop, disconnect, error).

    Uses its own DB session, shielded from cancellation: the request's own
    session may be mid-cancellation when the client has gone away.
    """
    with anyio.CancelScope(shield=True):
        try:
            async with get_session() as db:
                await _save_answer(
                    db,
                    conversation_id,
                    user_id,
                    content=content,
                    answer_mode=answer_mode,
                    citations=[],
                    tokens=tokens,
                    message_status=message_status,
                )
        except Exception:
            logger.exception(
                "Could not store an unfinished answer in conversation %s",
                conversation_id,
            )


async def _maybe_title(
    db: AsyncSession,
    llm: LiteLLMClient,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
) -> str | None:
    """Name a conversation after its first question. Returns the new title."""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.title != DEFAULT_TITLE:
        return None
    if get_settings().TITLE_GENERATION_ENABLED:
        await db.commit()  # no connection held during the LLM call
        title, tokens = await generate_title(llm, question)
    else:
        title, tokens = fallback_title(question), 0
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.title == DEFAULT_TITLE)
        .values(title=title)
    )
    await record_usage(db, tokens, user_id)
    await db.commit()
    return title


async def refresh_summary(
    llm: LiteLLMClient, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Fold turns that left the recent-context window into the rolling summary.

    Runs after the response is sent. Summarises only when at least
    CONVERSATION_SUMMARY_BATCH new messages have left the window, so a long
    chat costs one small summary call every few turns, not every turn.
    """
    settings = get_settings()
    window = max(0, settings.MAX_RECENT_CONTEXT_TURNS) * 2
    if window == 0:
        return
    try:
        async with get_session() as db:
            conversation = await db.get(Conversation, conversation_id)
            if conversation is None:
                return
            messages = [
                m
                for m in await _messages(db, conversation_id)
                if m.status == MessageStatus.COMPLETE
            ]
            covered = conversation.summary_message_count
            outside = len(messages) - window
            if outside - covered < max(1, settings.CONVERSATION_SUMMARY_BATCH):
                return
            batch = [
                {"role": m.role, "content": m.content}
                for m in messages[covered:outside]
            ]
            previous = conversation.summary
            await db.commit()
            summary, tokens = await update_summary(llm, previous, batch)
            if summary:
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(summary=summary, summary_message_count=outside)
                )
            await record_usage(db, tokens, user_id)
            await db.commit()
    except Exception:
        logger.exception(
            "Could not update the summary of conversation %s", conversation_id
        )


def _filters(body: SendMessageRequest) -> dict[str, Any] | None:
    filters = body.filters.model_dump(exclude_none=True) if body.filters else None
    try:
        validate_filters(filters)
    except InvalidFilterError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return filters


async def _answer(
    db: AsyncSession,
    pipeline: GenerationPipeline,
    user: SignedInUser,
    conversation: Conversation,
    question: Message,
    filters: dict[str, Any] | None,
    stream: bool,
) -> Any:
    """Answer `question` (already stored) as SSE or as one JSON response."""
    messages = await _messages(db, conversation.id)
    history = _history_before(messages, question)
    summary = conversation.summary
    first_question = not any(m.role == "assistant" for m in messages)
    conversation_id, user_id = conversation.id, user.id
    await db.commit()  # no pooled connection held while the model works
    llm = pipeline.llm_client
    background = BackgroundTask(refresh_summary, llm, conversation_id, user_id)

    if not stream:
        meter = UsageMeter()
        try:
            response: RAGResponse = await pipeline.aquery(
                db,
                question.content,
                history,
                filters,
                None,
                meter=meter,
                summary=summary,
            )
        except Exception:
            await _save_unfinished(
                conversation_id, user_id, "", None, meter.tokens, MessageStatus.ERROR
            )
            raise
        saved, used_today = await _save_answer(
            db,
            conversation_id,
            user_id,
            content=response.answer,
            answer_mode=response.answer_mode,
            citations=response.citations,
            tokens=response.tokens_used,
            message_status=MessageStatus.COMPLETE,
        )
        assert saved is not None
        title = (
            await _maybe_title(db, llm, conversation_id, user_id, question.content)
            if first_question
            else None
        )
        await refresh_summary(llm, conversation_id, user_id)
        return SendMessageResponse(
            user_message=_stored(question),
            assistant_message=_stored(saved),
            title=title,
            quota_remaining=_quota_remaining(used_today),
        )

    async def events() -> AsyncIterator[str]:
        meter = UsageMeter()
        streamed: list[str] = []
        mode: str | None = None
        saved = False
        failed = False
        try:
            yield _sse(
                "start",
                {
                    "conversation_id": str(conversation_id),
                    "user_message": _stored(question).model_dump(mode="json"),
                },
            )
            async for event in pipeline.astream_query(
                db,
                question.content,
                history,
                filters,
                None,
                meter=meter,
                summary=summary,
            ):
                kind = event.pop("type")
                if kind == "token":
                    streamed.append(event["content"])
                    yield _sse("token", event["content"])
                elif kind == "reset":
                    streamed.clear()
                    yield _sse("reset", {})
                elif kind == "mode":
                    mode = event["answer_mode"]
                    yield _sse("mode", event)
                elif kind == "citations":
                    yield _sse("citations", event["citations"])
                elif kind == "end":
                    message, used_today = await _save_answer(
                        db,
                        conversation_id,
                        user_id,
                        content=event["final_answer"],
                        answer_mode=event["answer_mode"],
                        citations=event["citations"],
                        tokens=event["tokens_used"],
                        message_status=MessageStatus.COMPLETE,
                    )
                    saved = True
                    event["message"] = (
                        _stored(message).model_dump(mode="json") if message else None
                    )
                    event["quota_remaining"] = _quota_remaining(used_today)
                    yield _sse("end", event)
            if saved and first_question:
                title = await _maybe_title(
                    db, llm, conversation_id, user_id, question.content
                )
                if title:
                    yield _sse(
                        "title",
                        {"conversation_id": str(conversation_id), "title": title},
                    )
        except Exception:
            failed = True
            logger.exception(
                "Streaming answer failed in conversation %s", conversation_id
            )
            yield _sse(
                "error",
                {
                    "detail": "The assistant is temporarily unavailable. Please try again."
                },
            )
        finally:
            # Runs on success, error AND client disconnect (cancellation).
            if not saved:
                await _save_unfinished(
                    conversation_id,
                    user_id,
                    "".join(streamed),
                    mode,
                    meter.tokens,
                    MessageStatus.ERROR if failed else MessageStatus.STOPPED,
                )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
        background=background,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SendMessageResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
    dependencies=[Depends(require_csrf)],
)
@limiter.limit(lambda: get_settings().USER_CHAT_RATE_LIMIT, key_func=session_rate_key)
async def send_message(
    request: Request,
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
    pipeline: GenerationPipeline = Depends(get_pipeline_dep),
) -> Any:
    filters = _filters(body)
    content = body.content.strip()
    if not content:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The question is empty."
        )
    conversation = await _owned_conversation(db, user, conversation_id)
    await check_allowance(db, user.id)
    question = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role="user",
        content=content,
        created_at=_now(),
    )
    db.add(question)
    await _touch(db, conversation.id)
    await db.commit()
    return await _answer(
        db, pipeline, user, conversation, question, filters, body.stream
    )


@router.post(
    "/messages/{message_id}/regenerate",
    response_model=SendMessageResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
    dependencies=[Depends(require_csrf)],
)
@limiter.limit(lambda: get_settings().USER_CHAT_RATE_LIMIT, key_func=session_rate_key)
async def regenerate(
    request: Request,
    message_id: uuid.UUID,
    stream: bool = Query(True),
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
    pipeline: GenerationPipeline = Depends(get_pipeline_dep),
) -> Any:
    """Answer the latest question again. `message_id` is the latest message:
    the answer to replace, or a question whose answer never arrived."""
    message = await db.get(Message, message_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found.")
    conversation = await _owned_conversation(db, user, message.conversation_id)
    messages = await _messages(db, conversation.id)
    if not messages or messages[-1].id != message.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only the latest answer can be regenerated."
        )
    if message.role == "assistant":
        questions = [m for m in messages[:-1] if m.role == "user"]
        if not questions:
            raise HTTPException(status.HTTP_409_CONFLICT, "No question to answer.")
        question = questions[-1]
    else:
        question = message
    await check_allowance(db, user.id)
    if message.role == "assistant":
        await db.delete(message)
        await db.commit()
    return await _answer(db, pipeline, user, conversation, question, None, stream)


@router.post(
    "/messages/{message_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def message_feedback(
    message_id: uuid.UUID,
    body: FeedbackRequest,
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    message = await db.get(Message, message_id)
    if message is None or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found.")
    await _owned_conversation(db, user, message.conversation_id)
    if body.rating is None:
        await db.execute(
            delete(MessageFeedback).where(
                MessageFeedback.message_id == message_id,
                MessageFeedback.user_id == user.id,
            )
        )
    else:
        comment = (body.comment or "").strip() or None
        statement = insert(MessageFeedback).values(
            message_id=message_id, user_id=user.id, rating=body.rating, comment=comment
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[MessageFeedback.message_id, MessageFeedback.user_id],
                set_={
                    "rating": body.rating,
                    "comment": comment,
                    "updated_at": func.now(),
                },
            )
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ import
_CITATION_KEYS = (
    "source_index",
    "chunk_id",
    "document_id",
    "title",
    "page_number",
    "score",
    "snippet",
)


@router.post(
    "/conversations/import",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def import_conversations(
    body: ImportRequest,
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ImportResult:
    """Copy a guest's browser-kept chats into the account.

    Imported answers are flagged `is_imported`: they are shown as they were,
    and were not re-checked against today's documents.
    """
    ids: list[uuid.UUID] = []
    now = _now()
    for item in body.conversations:
        started = min(_aware(item.created_at) or now, now)
        conversation = Conversation(
            id=uuid.uuid4(),
            user_id=user.id,
            title=(item.title or "").strip()[:200] or "Imported chat",
            created_at=started,
        )
        db.add(conversation)
        for index, m in enumerate(item.messages):
            mode = m.answer_mode if m.answer_mode in AnswerMode.ALL else None
            db.add(
                Message(
                    id=uuid.uuid4(),
                    conversation_id=conversation.id,
                    role=m.role,
                    content=m.content,
                    answer_mode=mode if m.role == "assistant" else None,
                    citations_json=[
                        c.model_dump(include=set(_CITATION_KEYS)) for c in m.citations
                    ]
                    if m.role == "assistant"
                    else [],
                    status=MessageStatus.COMPLETE,
                    is_imported=True,
                    # Keep the original order even when timestamps are missing.
                    created_at=started + timedelta(microseconds=index),
                )
            )
        ids.append(conversation.id)
    await db.commit()
    return ImportResult(imported=len(ids), conversation_ids=ids)
