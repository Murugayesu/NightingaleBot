import logging
import uuid

import procrastinate

from nursing_bot.api.dependencies import get_llm_client_dep, get_storage_dep
from nursing_bot.config import get_settings
from nursing_bot.ingestion.pipeline import IngestionPipeline
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import Document

logger = logging.getLogger(__name__)

settings = get_settings()
pg_conninfo = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=pg_conninfo),
    import_paths=["nursing_bot.workers.tasks"],
)


@app.task(queue="ingest")
async def ingest_document_task(document_id: str) -> None:
    """Procrastinate background worker task to ingest, chunk, embed, and index a document."""
    doc_uuid = uuid.UUID(document_id)
    storage = get_storage_dep()
    llm_client = get_llm_client_dep()

    try:
        async with get_session() as session:
            pipeline = IngestionPipeline(llm_client=llm_client, storage=storage)
            await pipeline.ingest_document(session, doc_uuid)
            logger.info("Successfully ingested document %s in worker.", document_id)
    except Exception as exc:
        logger.exception("Failed to ingest document %s in background worker.", document_id)
        async with get_session() as session:
            document = await session.get(Document, doc_uuid)
            if document is not None:
                document.status = "failed"
                document.error_message = str(exc)
                await session.commit()
        raise
