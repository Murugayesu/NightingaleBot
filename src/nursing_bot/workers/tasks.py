"""Procrastinate worker: tasks are stored in PostgreSQL (no Redis/RabbitMQ needed).

Run with:  procrastinate --app=nursing_bot.workers.tasks.app worker
The schema is created by `python -m nursing_bot.cli migrate`.
"""

import logging
import uuid

import procrastinate

from nursing_bot import jobs
from nursing_bot.config import get_settings
from nursing_bot.ingestion.pipeline import DocumentNotFoundError
from nursing_bot.observability import configure_logging, setup_tracing

_settings = get_settings()
configure_logging(_settings.LOG_LEVEL, _settings.LOG_FORMAT)
logger = logging.getLogger(__name__)


def _conninfo(database_url: str) -> str:
    """SQLAlchemy URL → libpq URL (drop the '+asyncpg' driver suffix)."""
    scheme, sep, rest = database_url.partition("://")
    return f"{scheme.split('+', 1)[0]}{sep}{rest}"


app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(
        conninfo=_conninfo(_settings.DATABASE_URL)
    ),
    import_paths=["nursing_bot.workers.tasks"],
)


async def ensure_queue_schema() -> bool:
    """Create the Procrastinate tables if missing. Returns True if it created them."""
    from sqlalchemy import text

    from nursing_bot.storage.database import get_session

    async with get_session() as session:
        exists = (
            await session.execute(text("SELECT to_regclass('procrastinate_jobs')"))
        ).scalar()
    if exists:
        return False
    async with app.open_async():
        await app.schema_manager.apply_schema_async()
    return True


@app.task(queue="ingest", retry=procrastinate.RetryStrategy(max_attempts=3, wait=30))
async def ingest_document_task(document_id: str) -> None:
    setup_tracing("nursing-bot-worker")
    try:
        await jobs.run_ingestion(uuid.UUID(document_id))
    except DocumentNotFoundError:
        # Deleted after it was queued: nothing to do, and nothing to retry.
        logger.info("Skipping ingestion of deleted document %s", document_id)


@app.task(queue="eval")
async def run_eval_task(run_id: str) -> None:
    setup_tracing("nursing-bot-worker")
    await jobs.run_eval(uuid.UUID(run_id))


@app.periodic(cron="17 3 * * *")  # daily at 03:17
@app.task(queue="maintenance")
async def purge_chat_history_task(timestamp: int) -> None:
    await jobs.purge_expired_chat_history()


@app.periodic(cron="*/5 * * * *")
@app.task(queue="maintenance", queueing_lock="retry_stalled_jobs")
async def retry_stalled_jobs_task(timestamp: int) -> None:
    """Re-queue jobs whose worker died mid-job (crash, redeploy, VM reboot).

    Without this, such a job stays "doing" forever and its document stays
    "processing". Workers send a heartbeat every few seconds; a job whose
    worker has been silent for 2 minutes is considered abandoned.
    """
    stalled = list(await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=120))
    for job in stalled:
        logger.warning("Re-queueing stalled job %s (%s)", job.id, job.task_name)
        await app.job_manager.retry_job(job)
