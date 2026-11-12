"""Admin system endpoints: health, statistics, re-indexing, evaluation runs."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.api.auth import require_admin
from nursing_bot.api.dependencies import get_db, get_llm_client_dep, get_storage_dep
from nursing_bot.api.schemas import (
    EvalRunDetail,
    EvalRunQueued,
    EvalRunRequest,
    EvalRunSummary,
    HealthResponse,
    ReindexResponse,
    SystemStatsResponse,
)
from nursing_bot.config import get_settings
from nursing_bot.evaluation.runner import create_eval_run
from nursing_bot.jobs import dispatch_eval, dispatch_ingestion
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.storage.models import (
    ChatMessage,
    ChatSession,
    Chunk,
    Conversation,
    DailyTokenUsage,
    Document,
    DocumentStatus,
    EvalRun,
    Message,
    User,
)
from nursing_bot.storage.object_store import S3Backend
from nursing_bot.usage import GLOBAL, today

router = APIRouter(
    prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)]
)


def _short_error(exc: Exception) -> str:
    return f"unhealthy: {type(exc).__name__}: {str(exc)[:200]}"


@router.get("/health", response_model=HealthResponse)
async def admin_health(
    db: AsyncSession = Depends(get_db),
    storage: S3Backend = Depends(get_storage_dep),
    llm: LiteLLMClient = Depends(get_llm_client_dep),
) -> HealthResponse:
    """Always 200: the body reports which components are degraded."""
    settings = get_settings()
    database, stale = "ok", 0
    try:
        await db.execute(text("SELECT 1"))
        stale = (
            await db.execute(
                select(func.count(Chunk.id)).where(
                    Chunk.embedding_model != llm.embedding_model
                )
            )
        ).scalar_one()
    except Exception as exc:  # noqa: BLE001
        database = _short_error(exc)
        await db.rollback()

    try:
        await storage.check()
        storage_status = "ok"
    except Exception as exc:  # noqa: BLE001
        storage_status = _short_error(exc)

    if settings.INGESTION_MODE != "queue":
        queue = "disabled (inline mode)"
    else:
        try:
            pending = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM procrastinate_jobs WHERE status IN ('todo', 'doing')"
                    )
                )
            ).scalar_one()
            queue = f"ok ({pending} jobs waiting or running)"
        except Exception as exc:  # noqa: BLE001
            queue = _short_error(exc)
            await db.rollback()

    healthy = (
        database == "ok"
        and storage_status == "ok"
        and not queue.startswith("unhealthy")
    )
    return HealthResponse(
        status="ok" if healthy else "degraded",
        environment=settings.ENVIRONMENT,
        database=database,
        storage=storage_status,
        queue=queue,
        llm_model=settings.LLM_MODEL,
        embedding_model=llm.embedding_model,
        stale_embedding_chunks=stale,
    )


@router.get("/stats", response_model=SystemStatsResponse)
async def admin_stats(
    db: AsyncSession = Depends(get_db), llm: LiteLLMClient = Depends(get_llm_client_dep)
) -> SystemStatsResponse:
    rows = await db.execute(
        select(Document.status, func.count()).group_by(Document.status)
    )
    by_status: dict[str, int] = {status_: int(n) for status_, n in rows.all()}

    async def count(column: Any) -> int:
        return int((await db.execute(select(func.count(column)))).scalar_one())

    since = datetime.now(UTC) - timedelta(days=7)
    modes = await db.execute(
        select(Message.answer_mode, func.count())
        .where(
            Message.role == "assistant",
            Message.answer_mode.is_not(None),
            Message.is_imported.is_(False),
            Message.created_at >= since,
        )
        .group_by(Message.answer_mode)
    )
    tokens_today = await db.scalar(
        select(DailyTokenUsage.tokens).where(
            DailyTokenUsage.day == today(), DailyTokenUsage.subject == GLOBAL
        )
    )
    return SystemStatsResponse(
        total_documents=sum(by_status.values()),
        documents_by_status=by_status,
        total_chunks=await count(Chunk.id),
        total_sessions=await count(ChatSession.id),
        total_messages=await count(ChatMessage.id),
        total_users=await count(User.id),
        total_conversations=await count(Conversation.id),
        answers_last_7_days={str(mode): int(n) for mode, n in modes.all()},
        tokens_today=int(tokens_today or 0),
        active_embedding_model=llm.embedding_model,
    )


@router.post(
    "/reindex", response_model=ReindexResponse, status_code=status.HTTP_202_ACCEPTED
)
async def reindex_all(
    background: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> ReindexResponse:
    """Re-ingest every document, e.g. after changing the embedding model or chunking."""
    documents = list((await db.execute(select(Document))).scalars())
    for doc in documents:
        doc.status = DocumentStatus.PENDING
        doc.error_message = None
    await db.commit()
    for doc in documents:
        await dispatch_ingestion(doc.id, background)
    return ReindexResponse(queued=len(documents))


@router.post(
    "/eval/runs", response_model=EvalRunQueued, status_code=status.HTTP_202_ACCEPTED
)
async def start_eval_run(
    body: EvalRunRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> EvalRunQueued:
    run = await create_eval_run(db, body.mode, use_judge=body.judge)
    await dispatch_eval(run.id, background)
    return EvalRunQueued(id=run.id, status=run.status)


@router.get("/eval/runs", response_model=list[EvalRunSummary])
async def list_eval_runs(
    limit: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db)
) -> list[EvalRunSummary]:
    rows = await db.execute(
        select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit)
    )
    return [EvalRunSummary.model_validate(r) for r in rows.scalars()]


@router.get("/eval/runs/{run_id}", response_model=EvalRunDetail)
async def get_eval_run(
    run_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> EvalRunDetail:
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eval run not found.")
    return EvalRunDetail.model_validate(run)
