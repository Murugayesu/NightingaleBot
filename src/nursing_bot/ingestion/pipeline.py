import logging
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.ingestion.context import add_context_headers
from nursing_bot.ingestion.loaders import DocumentLoaderFactory
from nursing_bot.ingestion.splitters import StructureSplitter
from nursing_bot.ingestion.tabular import TabularCardGenerator
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.storage.models import Chunk, Document
from nursing_bot.storage.object_store import S3Backend

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """End-to-end ingestion pipeline using LangChain loaders and structure-aware chunking."""

    def __init__(
        self,
        llm_client: LiteLLMClient,
        storage: S3Backend,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        embedding_batch_size: int | None = None,
        splitter: StructureSplitter | None = None,
    ):
        settings = get_settings()
        self.llm_client = llm_client
        self.storage = storage
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_batch_size = (
            embedding_batch_size or settings.EMBEDDING_BATCH_SIZE
        )
        self.splitter = splitter or StructureSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )

    async def ingest_document(
        self,
        session: AsyncSession,
        document_id: uuid.UUID,
    ) -> Document:
        """Ingest, parse, chunk, embed, and index a document by its ID."""
        stmt = select(Document).where(Document.id == document_id)
        res = await session.execute(stmt)
        doc = res.scalar_one_or_none()
        if not doc:
            raise ValueError(f"Document {document_id} not found")

        try:
            doc.status = "processing"
            await session.flush()

            raw_bytes = await self.storage.download(doc.s3_key)

            if doc.content_type == "tabular":
                suffix = doc.filename.lower().rsplit(".", 1)[-1]
                if suffix in {"csv", "tsv"}:
                    text_content = raw_bytes.decode("utf-8-sig", errors="replace")
                    lc_docs = TabularCardGenerator.from_csv(
                        text_content,
                        default_doc_type=doc.doc_type,
                        delimiter="\t" if suffix == "tsv" else ",",
                    )
                elif suffix == "xlsx":
                    lc_docs = TabularCardGenerator.from_xlsx(
                        raw_bytes,
                        default_doc_type=doc.doc_type,
                    )
                else:
                    text_content = raw_bytes.decode("utf-8-sig", errors="replace")
                    lc_docs = TabularCardGenerator.from_json(
                        text_content,
                        default_doc_type=doc.doc_type,
                    )
            else:
                raw_parsed_docs = DocumentLoaderFactory.load_bytes(
                    raw_bytes, doc.filename
                )
                lc_docs = self.splitter.split_documents(raw_parsed_docs)

            if not lc_docs:
                raise ValueError(f"No extractable content found in '{doc.filename}'.")

            contextualized_docs = [
                add_context_headers(d, document_title=doc.filename) for d in lc_docs
            ]

            texts = [d.page_content for d in contextualized_docs]
            all_embeddings: list[list[float]] = []
            for i in range(0, len(texts), self.embedding_batch_size):
                batch = texts[i : i + self.embedding_batch_size]
                vectors = await self.llm_client.aembed_texts(batch)
                all_embeddings.extend(vectors)

            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))

            for idx, (cdoc, vector) in enumerate(
                zip(contextualized_docs, all_embeddings)
            ):
                hierarchy = []
                for level in range(1, 5):
                    val = cdoc.metadata.get(f"header_{level}")
                    if val and isinstance(val, str):
                        hierarchy.append(val)

                raw_content = (
                    lc_docs[idx].page_content
                    if idx < len(lc_docs)
                    else cdoc.page_content
                )
                chunk = Chunk(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    chunk_index=idx,
                    content=cdoc.page_content,
                    contextualised_text=raw_content,
                    embedding=vector,
                    embedding_model=self.llm_client.embedding_model,
                    heading_hierarchy=hierarchy or [doc.filename],
                    page_number=cdoc.metadata.get("page"),
                    metadata_json={
                        "doc_type": doc.doc_type,
                        **doc.metadata_json,
                        **cdoc.metadata,
                    },
                    tsv=func.to_tsvector("english", cdoc.page_content),
                )
                session.add(chunk)

            doc.chunk_count = len(contextualized_docs)
            doc.status = "completed"
            doc.error_message = None
            await session.commit()
            return doc

        except Exception as exc:
            logger.exception("Failed to ingest document %s", document_id)
            doc.status = "failed"
            doc.error_message = str(exc)
            try:
                await session.commit()
            except Exception as commit_exc:  # noqa: BLE001
                logger.error(
                    "Failed to persist failed status for document %s: %s",
                    document_id,
                    commit_exc,
                )
            raise
