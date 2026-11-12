"""SQLAlchemy ORM models. The schema itself is owned by Alembic migrations.

Tables:
  documents          one row per uploaded file (raw bytes live in object storage)
  chunks             searchable pieces of a document: text + vector + tsvector
  users              accounts (email + Argon2id password hash, role)
  auth_sessions      signed-in browser sessions (only the token's hash is stored)
  conversations      a signed-in user's chats
  messages           the turns of a conversation, with answer mode and citations
  message_feedback   thumbs up / down on an answer
  daily_token_usage  LLM tokens per day, per user and for the whole service
  chat_sessions      anonymous guest sessions (token budget, expiry)
  chat_messages      optional PII-redacted guest history (retention-limited)
  eval_runs          results of retrieval / answer evaluations
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym

from nursing_bot.config import VECTOR_COLUMN_DIMENSIONS


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


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


class UserRole:
    USER = "user"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Stored lower-cased and unique (index in migration 004). A login name
    # only: there is no e-mail server, so it is never verified.
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Set by an admin password reset: the user must choose a new password
    # before doing anything else.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=UserRole.USER)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # Case-insensitive uniqueness, even for rows not written through the app.
    __table_args__ = (Index("uq_users_email_lower", text("lower(email)"), unique=True),)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    # SHA-256 of the cookie token: a database leak does not leak live sessions.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AnswerMode:
    GROUNDED = "grounded"  # from official documents, cited and verified
    GENERAL = "general"  # labelled general nursing knowledge, no official facts
    DECLINED = "declined"  # official question the documents do not answer
    REFUSED = "refused"  # personal medical advice, or a blocked request
    OUT_OF_SCOPE = "out_of_scope"  # not about nursing at all

    ALL = (GROUNDED, GENERAL, DECLINED, REFUSED, OUT_OF_SCOPE)


class MessageStatus:
    COMPLETE = "complete"
    STOPPED = "stopped"  # the user pressed Stop or the connection dropped
    ERROR = "error"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New chat")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Rolling summary of the turns older than the recent-context window, and
    # how many messages (oldest first) it already covers.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (Index("idx_conversations_user_updated", "user_id", "updated_at"),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    answer_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        "citations", JSONB, nullable=False, default=list
    )
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MessageStatus.COMPLETE
    )
    # Copied from a guest's browser at sign-up: shown, but never re-verified.
    is_imported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set by Python, not the database: now() is fixed for a whole transaction,
    # so turns inserted together would otherwise share one timestamp.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index("idx_messages_conversation_created", "conversation_id", "created_at"),
    )


class MessageFeedback(Base):
    __tablename__ = "message_feedback"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # +1 or -1
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class DailyTokenUsage(Base):
    """LLM tokens spent per UTC day. subject is "global" or "user:<uuid>"."""

    __tablename__ = "daily_token_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    subject: Mapped[str] = mapped_column(String(64), primary_key=True)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


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
