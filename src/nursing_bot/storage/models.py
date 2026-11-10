"""SQLAlchemy ORM models. The schema itself is owned by Alembic migrations.

Tables:
  documents      one row per uploaded file (raw bytes live in object storage)
  chunks         searchable pieces of a document: text + vector + tsvector
  chat_sessions  anonymous widget sessions (token budget, expiry)
  chat_messages  PII-redacted conversation history (optional, retention-limited)
  eval_runs      results of retrieval / answer evaluations
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym

from nursing_bot.config import VECTOR_COLUMN_DIMENSIONS


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DocumentStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    s3_version_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # SHA-256 of the file bytes. Unique: the same file is never stored twice.
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    content_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # prose | tabular
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DocumentStatus.PENDING, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # API-facing names for the two columns above (usable in constructors too).
    chunk_count = synonym("total_chunks")
    error_message = synonym("failure_reason")

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Raw chunk text: what users see in citations.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Context-prefixed text: what is embedded and what the tsv trigger indexes.
    contextualised_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(VECTOR_COLUMN_DIMENSIONS), nullable=False
    )
    # Dense search only compares vectors from the currently configured model.
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-based
    heading_hierarchy: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", index=True
    )
    # Maintained by the chunks_tsv_update() trigger; never written by the app.
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "idx_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_chunks_tsv_gin", "tsv", postgresql_using="gin"),
        # jsonb_path_ops: smaller and faster for the `@>` containment used by
        # retrieval/metadata.py (does not support key-existence operators).
        Index(
            "idx_chunks_metadata_gin",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
        Index("idx_chunks_doc_status", "document_id", "status"),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[list[dict[str, Any]] | None] = mapped_column(
        "citations", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)  # retrieval | answer
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    config_json: Mapped[dict[str, Any]] = mapped_column(
        "config", JSONB, nullable=False, default=dict
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        "metrics", JSONB, nullable=False, default=dict
    )
    results_json: Mapped[list[dict[str, Any]]] = mapped_column(
        "results", JSONB, nullable=False, default=list
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
