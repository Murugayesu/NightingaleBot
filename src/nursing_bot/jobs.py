"""Background jobs and how they are dispatched.

Each job opens its own database session, so it can run anywhere:
  * in the Procrastinate worker (INGESTION_MODE=queue, the docker default)
  * as a FastAPI background task in the API process (INGESTION_MODE=inline)
  * from the CLI
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import delete, or_

from nursing_bot.config import get_settings
from nursing_bot.ingestion.pipeline import IngestionPipeline
from nursing_bot.services import get_llm_client, get_storage
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import (
    AuthSession,
    ChatSession,
    Conversation,
    DailyTokenUsage,
    Document,
    DocumentStatus,
    EvalRun,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- the jobs
async def run_ingestion(document_id: uuid.UUID) -> None:
    async with get_session() as session:
        pipeline = IngestionPipeline(llm_client=get_llm_client(), storage=get_storage())
        await pipeline.ingest_document(session, document_id)


async def run_eval(run_id: uuid.UUID) -> None:
    from nursing_bot.evaluation.runner import (
        execute_eval_run,
    )  # heavy import, keep lazy

    await execute_eval_run(run_id)


async def purge_expired_chat_history() -> int:
    """Delete chat sessions (and their messages) past the retention period."""
    days = get_settings().CHAT_HISTORY_RETENTION_DAYS
    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with get_session() as session:
        result = await session.execute(
            delete(ChatSession).where(ChatSession.updated_at < cutoff)
        )
        await session.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    logger.info("Purged %d chat sessions older than %d days", deleted, days)
    return deleted


USAGE_HISTORY_DAYS = 90


async def purge_expired_account_data() -> dict[str, int]:
    """Delete ended sign-in sessions, old daily usage counters and, when
    CONVERSATION_RETENTION_DAYS is set, conversations inactive for longer."""
    now = datetime.now(UTC)
    counts: dict[str, int] = {}
    async with get_session() as session:
        result = await session.execute(
            delete(AuthSession).where(
                or_(
                    AuthSession.expires_at < now,
                    AuthSession.revoked_at < now - timedelta(days=1),
                )
            )
        )
        counts["auth_sessions"] = int(getattr(result, "rowcount", 0) or 0)
        result = await session.execute(
            delete(DailyTokenUsage).where(
                DailyTokenUsage.day < (now - timedelta(days=USAGE_HISTORY_DAYS)).date()
            )
        )
        counts["usage_days"] = int(getattr(result, "rowcount", 0) or 0)
        days = get_settings().CONVERSATION_RETENTION_DAYS
        counts["conversations"] = 0
        if days > 0:
            result = await session.execute(
                delete(Conversation).where(
                    Conversation.updated_at < now - timedelta(days=days)
                )
            )
            counts["conversations"] = int(getattr(result, "rowcount", 0) or 0)
        await session.commit()
    logger.info("Purged expired account data: %s", counts)
    return counts


# --------------------------------------------------------------- dispatching
async def _safe(job_name: str, coro: object) -> None:
    try:
        await coro  # type: ignore[misc]
    except Exception:
        logger.exception("Background job %s failed", job_name)


async def dispatch_ingestion(
    document_id: uuid.UUID, background: BackgroundTasks | None = None
) -> None:
    """Queue mode: defer to the worker. Inline mode: run after the response.

    If the queue is unreachable the document is marked failed with the reason,
    so it is visible in the admin panel and can be re-ingested, never stuck
    in 'pending'.
    """
    if get_settings().INGESTION_MODE == "queue":
        try:
            from nursing_bot.workers.tasks import ingest_document_task

            await ingest_document_task.defer_async(document_id=str(document_id))
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not enqueue ingestion of %s: %s", document_id, exc)
            await _mark_document_failed(
                document_id, f"Could not enqueue ingestion job: {exc}"
            )
        return
    if background is not None:
        background.add_task(_safe, "ingestion", run_ingestion(document_id))
    else:
        await _safe("ingestion", run_ingestion(document_id))


async def dispatch_eval(
    run_id: uuid.UUID, background: BackgroundTasks | None = None
) -> None:
    if get_settings().INGESTION_MODE == "queue":
        try:
            from nursing_bot.workers.tasks import run_eval_task

            await run_eval_task.defer_async(run_id=str(run_id))
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not enqueue eval run %s: %s", run_id, exc)
            await _mark_eval_failed(run_id, f"Could not enqueue eval job: {exc}")
            return
    if background is not None:
        background.add_task(_safe, "eval", run_eval(run_id))
    else:
        await _safe("eval", run_eval(run_id))


async def _mark_document_failed(document_id: uuid.UUID, reason: str) -> None:
    async with get_session() as session:
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status = DocumentStatus.FAILED
            doc.error_message = reason
            await session.commit()


async def _mark_eval_failed(run_id: uuid.UUID, reason: str) -> None:
    async with get_session() as session:
        run = await session.get(EvalRun, run_id)
        if run is not None:
            run.status = "failed"
            run.error = reason
            run.finished_at = datetime.now(UTC)
            await session.commit()
