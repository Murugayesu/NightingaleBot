"""Admin document management: upload, list, inspect, re-ingest, delete."""

import hashlib
import json
import logging
import re
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.api.auth import require_admin
from nursing_bot.api.dependencies import get_db, get_storage_dep
from nursing_bot.api.schemas import (
    ChunkResponse,
    DocumentListResponse,
    DocumentResponse,
)
from nursing_bot.config import get_settings
from nursing_bot.ingestion.loaders import (
    TABULAR_SUFFIXES,
    file_suffix,
    unsupported_reason,
)
from nursing_bot.jobs import dispatch_ingestion, run_ingestion
from nursing_bot.storage.models import Chunk, Document, DocumentStatus
from nursing_bot.storage.object_store import S3Backend

router = APIRouter(
    prefix="/documents", tags=["Documents"], dependencies=[Depends(require_admin)]
)
logger = logging.getLogger(__name__)

_READ_BLOCK = 1024 * 1024
_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9._ ()\-]+")


async def _read_upload_bounded(file: UploadFile, max_size: int) -> bytes:
    """Read at most max_size bytes; 413 as soon as the limit is exceeded."""
    too_large = HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"Upload exceeds the {max_size}-byte limit.",
    )
    if file.size is not None and file.size > max_size:
        raise too_large
    data = bytearray()
    while block := await file.read(min(_READ_BLOCK, max_size + 1 - len(data))):
        data.extend(block)
        if len(data) > max_size:
            raise too_large
    return bytes(data)


def _parse_metadata(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"metadata_json is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "metadata_json must be a JSON object."
        )
    return parsed


async def _get_document(db: AsyncSession, document_id: uuid.UUID) -> Document:
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return doc


async def _start_ingestion(
    db: AsyncSession, doc: Document, background: BackgroundTasks
) -> None:
    """Inline mode ingests during the request; queue mode hands off to the worker."""
    if get_settings().INGESTION_MODE == "inline":
        try:
            await run_ingestion(doc.id)
        # The failure is recorded on the document; the upload itself succeeded.
        except Exception as exc:  # noqa: BLE001
            logger.warning("Inline ingestion failed for %s: %s", doc.id, exc)
    else:
        await dispatch_ingestion(doc.id, background)
    db.expire_all()


@router.post(
    "/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    response: Response,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: str = Form(default="general", max_length=64),
    metadata_json: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    storage: S3Backend = Depends(get_storage_dep),
) -> Document:
    filename = (file.filename or "").strip() or "unnamed"
    if reason := unsupported_reason(filename):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, reason)
    metadata = _parse_metadata(metadata_json)
    data = await _read_upload_bounded(file, get_settings().MAX_UPLOAD_SIZE_BYTES)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty.")

    content_hash = hashlib.sha256(data).hexdigest()
    existing = (
        await db.execute(select(Document).where(Document.content_hash == content_hash))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status == DocumentStatus.FAILED:
            # Same bytes failed before: try again instead of returning the failure.
            existing_id = existing.id  # read before _start_ingestion expires the object
            existing.status = DocumentStatus.PENDING
            existing.error_message = None
            await db.commit()
            await _start_ingestion(db, existing, background)
            return await _get_document(db, existing_id)
        response.status_code = status.HTTP_200_OK  # duplicate of a healthy document
        return existing

    doc_id = uuid.uuid4()
    safe_name = _UNSAFE_KEY_CHARS.sub("_", filename)[:200]
    s3_key = f"{doc_id}/{safe_name}"
    await storage.upload(s3_key, data, content_type=file.content_type)

    doc = Document(
        id=doc_id,
        filename=filename[:512],
        doc_type=doc_type.strip() or "general",
        content_type="tabular"
        if file_suffix(filename) in TABULAR_SUFFIXES
        else "prose",
        s3_key=s3_key,
        content_hash=content_hash,
        status=DocumentStatus.PENDING,
        chunk_count=0,
        metadata_json=metadata,
    )
    db.add(doc)
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent upload of the same file won the race: keep theirs.
        await db.rollback()
        await _delete_object_quietly(storage, s3_key)
        winner = (
            await db.execute(
                select(Document).where(Document.content_hash == content_hash)
            )
        ).scalar_one()
        response.status_code = status.HTTP_200_OK
        return winner
    except Exception:
        await _delete_object_quietly(storage, s3_key)
        raise

    await _start_ingestion(db, doc, background)
    return await _get_document(db, doc_id)


async def _delete_object_quietly(storage: S3Backend, key: str) -> None:
    try:
        await storage.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not delete object %s: %s", key, exc)


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    doc_type: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    if page_size is not None:
        limit = page_size
    if page is not None:
        offset = (page - 1) * limit
    stmt = select(Document)
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if status_filter:
        stmt = stmt.where(Document.status == status_filter)
    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars()
    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(d) for d in rows], total=total
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Document:
    return await _get_document(db, document_id)


@router.post(
    "/{document_id}/reingest",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reingest_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> Document:
    doc = await _get_document(db, document_id)
    doc.status = DocumentStatus.PENDING
    doc.error_message = None
    await db.commit()
    await dispatch_ingestion(doc.id, background)
    db.expire_all()
    return await _get_document(db, document_id)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    storage: S3Backend = Depends(get_storage_dep),
) -> Response:
    doc = await _get_document(db, document_id)
    s3_key = doc.s3_key
    await db.delete(doc)  # chunks are removed by ON DELETE CASCADE
    await db.commit()
    # Delete the file only after the database commit succeeded, so a failure
    # never leaves a document row pointing at a missing file.
    await _delete_object_quietly(storage, s3_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{document_id}/chunks", response_model=list[ChunkResponse])
async def get_document_chunks(
    document_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[Chunk]:
    await _get_document(db, document_id)
    rows = await db.execute(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index)
        .limit(limit)
        .offset(offset)
    )
    return list(rows.scalars())
