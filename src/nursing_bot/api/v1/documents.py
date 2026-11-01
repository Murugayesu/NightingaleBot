import hashlib
import json
import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.api.dependencies import get_db, get_llm_client_dep, get_storage_dep
from nursing_bot.api.schemas import (
    ChunkResponse,
    DocumentListResponse,
    DocumentResponse,
)
from nursing_bot.api.security import verify_admin_key
from nursing_bot.config import get_settings
from nursing_bot.ingestion.pipeline import IngestionPipeline
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.storage.models import Chunk, Document
from nursing_bot.storage.object_store import S3Backend

router = APIRouter(prefix="/documents", tags=["Documents"])
logger = logging.getLogger(__name__)

UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


async def _read_upload_bounded(file: UploadFile) -> bytes:
    max_size = get_settings().MAX_UPLOAD_SIZE_BYTES
    if file.size is not None and file.size > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload exceeds the {max_size}-byte limit.",
        )

    data = bytearray()
    while len(data) <= max_size:
        remaining = max_size + 1 - len(data)
        block = await file.read(min(UPLOAD_READ_CHUNK_BYTES, remaining))
        if not block:
            break
        data.extend(block)
    if len(data) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload exceeds the {max_size}-byte limit.",
        )
    return bytes(data)


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_admin_key)],
)
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Form(default="general"),
    metadata_json: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    storage: S3Backend = Depends(get_storage_dep),
    llm_client: LiteLLMClient = Depends(get_llm_client_dep),
):
    """Admin-only document upload endpoint with S3 storage and automatic ingestion."""
    data = await _read_upload_bounded(file)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    content_hash = hashlib.sha256(data).hexdigest()

    # Deduplication check: return existing document if identical file content was uploaded
    dup_stmt = select(Document).where(Document.content_hash == content_hash)
    dup_res = await db.execute(dup_stmt)
    existing_doc = dup_res.scalar_one_or_none()
    if existing_doc is not None:
        return existing_doc

    filename = file.filename or "unnamed_document"
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    content_type = "tabular" if suffix in {"csv", "tsv", "xlsx", "json"} else "prose"

    parsed_metadata = {}
    if metadata_json:
        try:
            parsed_metadata = json.loads(metadata_json)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid metadata JSON: {exc}"
            ) from exc

    doc_id = uuid.uuid4()
    s3_key = f"{doc_id}/{filename}"

    await storage.upload(s3_key, data, content_type=file.content_type)

    doc = Document(
        id=doc_id,
        filename=filename,
        doc_type=doc_type,
        content_type=content_type,
        s3_key=s3_key,
        content_hash=content_hash,
        status="pending",
        chunk_count=0,
        metadata_json=parsed_metadata,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    settings = get_settings()
    if settings.INGESTION_MODE == "inline":
        pipeline = IngestionPipeline(llm_client=llm_client, storage=storage)
        try:
            await pipeline.ingest_document(db, doc_id)
            await db.refresh(doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Inline ingestion failed for %s: %s", doc_id, exc)
    else:
        try:
            from nursing_bot.workers.tasks import ingest_document_task

            await ingest_document_task.defer_async(document_id=str(doc_id))
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Background queue unavailable, proceeding with pending status: %s", exc
            )

    return doc


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    doc_type: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Document)
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if status_filter:
        stmt = stmt.where(Document.status == status_filter)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    stmt = stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    res = await db.execute(stmt)
    documents = res.scalars().all()

    return DocumentListResponse(documents=list(documents), total=total)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Document).where(Document.id == document_id)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_admin_key)],
)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    storage: S3Backend = Depends(get_storage_dep),
):
    stmt = select(Document).where(Document.id == document_id)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        await storage.delete(doc.s3_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error deleting s3 object %s: %s", doc.s3_key, exc)

    await db.delete(doc)
    await db.commit()


@router.get("/{document_id}/chunks", response_model=list[ChunkResponse])
async def get_document_chunks(
    document_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index.asc())
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(stmt)
    return res.scalars().all()
