import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FilterPayload(BaseModel):
    doc_type: str | list[str] | None = None
    district: str | list[str] | None = None
    program: str | list[str] | None = None
    institution: str | list[str] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ChatContextTurn(BaseModel):
    role: Literal["user", "assistant"] = Field(
        ..., description="Role: 'user' or 'assistant'"
    )
    content: str = Field(..., max_length=2000, description="Message text")


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None
    stream: bool = False
    filters: FilterPayload | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    recent_context: list[ChatContextTurn] | None = Field(
        default=None,
        description="Client-side ephemeral context window (last 1-2 conversation turns) for website widgets.",
    )


class CitationItem(BaseModel):
    source_index: int
    chunk_id: str
    document_id: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationItem] = Field(default_factory=list)
    session_id: str
    is_grounded: bool = True
    gate_passed: bool = True
    top_score: float = 0.0
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    abstention_reason: str | None = None


class MessageItem(BaseModel):
    role: str
    content: str
    created_at: datetime | None = None


class ConversationHistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageItem]


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    doc_type: str
    content_type: str
    s3_key: str
    status: str
    chunk_count: int
    error_message: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    documents: list[DocumentResponse]
    total: int


class ChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    heading_hierarchy: list[str]
    page_number: int | None
    metadata_json: dict[str, Any]
    status: str


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str
    storage: str
    llm_model: str
    embedding_model: str


class SystemStatsResponse(BaseModel):
    total_documents: int
    total_chunks: int
    total_sessions: int
    active_embedding_model: str
