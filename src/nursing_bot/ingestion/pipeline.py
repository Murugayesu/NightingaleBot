"""Ingestion pipeline: object storage → parse → chunk → contextualise → embed → index.

    download ─► parse (thread) ─► chunk ─► context headers ─► embed (batches) ─► replace chunks
                                                                                 (one transaction)

Called by the upload endpoint (inline mode), by the worker (queue mode) and by
`nursing_bot.cli ingest-dir`. Re-running it on the same document is safe: old
chunks are replaced atomically, so search never sees a half-indexed document.
"""

import asyncio
import logging
import uuid

from langchain_core.documents import Document as LCDocument
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.ingestion.context import LLMContextualizer, add_context_headers
from nursing_bot.ingestion.loaders import TABULAR_SUFFIXES, file_suffix, load_prose
from nursing_bot.ingestion.splitters import StructureSplitter
from nursing_bot.ingestion.tabular import TabularCardGenerator
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.observability import tracer
from nursing_bot.storage.models import Chunk, Document, DocumentStatus
from nursing_bot.storage.object_store import S3Backend

logger = logging.getLogger(__name__)


class DocumentNotFoundError(LookupError):
    """The document was deleted after its ingestion job was queued."""


_MAX_ERROR_CHARS = 2000


def parse_document(
    data: bytes, filename: str, doc_type: str, splitter: StructureSplitter
) -> list[LCDocument]:
    """Bytes → chunk-sized LangChain documents. Synchronous (CPU-bound)."""
    suffix = file_suffix(filename)
    if suffix in TABULAR_SUFFIXES:
        if suffix == ".xlsx":
            return TabularCardGenerator.from_xlsx(data, default_doc_type=doc_type)
        text = data.decode("utf-8-sig", errors="replace")
        if suffix == ".json":
            return TabularCardGenerator.from_json(text, default_doc_type=doc_type)
        return TabularCardGenerator.from_csv(
            text, default_doc_type=doc_type, delimiter="\t" if suffix == ".tsv" else ","
        )
    return splitter.split_documents(load_prose(data, filename))


class IngestionPipeline:
    def __init__(
        self,
        llm_client: LiteLLMClient,
        storage: S3Backend,
        splitter: StructureSplitter | None = None,
        embedding_batch_size: int | None = None,
        contextualizer: LLMContextualizer | None = None,
    ):
        settings = get_settings()
        self.llm_client = llm_client
        self.storage = storage
        self.splitter = splitter or StructureSplitter(
            settings.CHUNK_SIZE, settings.CHUNK_OVERLAP
        )
        self.embedding_batch_size = (
            embedding_batch_size or settings.EMBEDDING_BATCH_SIZE
        )
        if contextualizer is None and settings.CONTEXTUAL_HEADERS_ENABLED:
            contextualizer = LLMContextualizer(
                llm_client,
                model=settings.CONTEXTUAL_HEADERS_MODEL,
                concurrency=settings.CONTEXTUAL_HEADERS_CONCURRENCY,
            )
        self.contextualizer = contextualizer

    async def _embed(
        self, texts: list[str], session: AsyncSession, document_id: uuid.UUID
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.embedding_batch_size):
            # Stop early if an admin deleted the document while it was being
            # embedded: no point spending more provider quota on it.
            if start and not await self._still_exists(session, document_id):
                raise DocumentNotFoundError(f"Document {document_id} was deleted")
            vectors.extend(
                await self.llm_client.aembed_texts(
                    texts[start : start + self.embedding_batch_size], patient=True
                )
            )
        return vectors

    @staticmethod
    async def _still_exists(session: AsyncSession, document_id: uuid.UUID) -> bool:
        row = await session.execute(
            select(Document.id).where(Document.id == document_id)
        )
        await (
            session.commit()
        )  # end the read so the check doesn't hold a transaction open
        return row.first() is not None

    async def ingest_document(
        self, session: AsyncSession, document_id: uuid.UUID
    ) -> Document:
        """Ingest one document. Marks it completed, or failed (and re-raises)."""
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            raise DocumentNotFoundError(f"Document {document_id} not found")

        # Commit "processing" immediately so the admin panel shows progress.
        doc.status = DocumentStatus.PROCESSING
        doc.error_message = None
        await session.commit()

        with tracer.start_as_current_span("ingestion.document") as span:
            span.set_attribute("ingestion.filename", doc.filename)
            try:
                raw = await self.storage.download(doc.s3_key)
                pieces = await asyncio.to_thread(
                    parse_document, raw, doc.filename, doc.doc_type, self.splitter
                )
                if not pieces:
                    raise ValueError(f"No extractable text found in '{doc.filename}'.")

                llm_context: list[str | None] = [None] * len(pieces)
                if self.contextualizer is not None:
                    full_text = "\n\n".join(p.page_content for p in pieces)
                    llm_context = await self.contextualizer.contextualize(
                        doc.filename, full_text, pieces
                    )

                contextualised = [
                    add_context_headers(piece, doc.filename, ctx)
                    for piece, ctx in zip(pieces, llm_context, strict=True)
                ]
                vectors = await self._embed(
                    [c.page_content for c in contextualised], session, doc.id
                )

                # Lock the document row until commit: if two jobs ingest the same
                # document at once, the second waits here and then replaces the
                # first one's chunks instead of adding a duplicate set.
                await session.execute(
                    select(Document.id).where(Document.id == doc.id).with_for_update()
                )
                await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
                for index, (piece, ctx_doc, vector) in enumerate(
                    zip(pieces, contextualised, vectors, strict=True)
                ):
                    hierarchy = [
                        str(piece.metadata[f"header_{level}"])
                        for level in range(1, 5)
                        if piece.metadata.get(f"header_{level}")
                    ]
                    session.add(
                        Chunk(
                            document_id=doc.id,
                            chunk_index=index,
                            content=piece.page_content,  # raw text, shown in citations
                            contextualised_text=ctx_doc.page_content,  # embedded + tsv-indexed
                            embedding=vector,
                            embedding_model=self.llm_client.embedding_model,
                            token_count=self.llm_client.count_tokens(
                                ctx_doc.page_content
                            ),
                            heading_hierarchy=hierarchy or [doc.filename],
                            page_number=piece.metadata.get("page"),
                            metadata_json={
                                "doc_type": doc.doc_type,
                                **doc.metadata_json,
                                **ctx_doc.metadata,
                                "source": doc.filename,
                            },
                        )
                    )
                doc.chunk_count = len(pieces)
                doc.status = DocumentStatus.COMPLETED
                await session.commit()
                span.set_attribute("ingestion.chunks", len(pieces))
                logger.info("Ingested %s: %d chunks", doc.filename, len(pieces))
                return doc
            except DocumentNotFoundError:
                await session.rollback()
                logger.info(
                    "Stopped ingesting %s: the document was deleted", document_id
                )
                raise
            except Exception as exc:
                logger.exception(
                    "Ingestion failed for %s (%s)", doc.filename, document_id
                )
                await session.rollback()
                await self._mark_failed(session, document_id, exc)
                raise

    @staticmethod
    async def _mark_failed(
        session: AsyncSession, document_id: uuid.UUID, exc: Exception
    ) -> None:
        try:
            doc = await session.get(Document, document_id)
            if doc is not None:
                doc.status = DocumentStatus.FAILED
                doc.error_message = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
                await session.commit()
        except Exception as commit_exc:  # noqa: BLE001
            logger.error("Could not record failure for %s: %s", document_id, commit_exc)
