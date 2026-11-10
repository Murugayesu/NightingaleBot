"""Pydantic request/response models. docs/api-contract.md documents the same shapes."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nursing_bot.generation.guardrails import MAX_QUERY_LENGTH


class FilterPayload(BaseModel):
    doc_type: str | list[str] | None = None
    district: str | list[str] | None = None
    program: str | list[str] | None = None
    institution: str | list[str] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ChatContextTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=2000)


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_LENGTH)
    stream: bool = False
    filters: FilterPayload | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    recent_context: list[ChatContextTurn] | None = Field(
        default=None,
        max_length=20,
        description="Last few user/assistant messages kept by the client, oldest first.",
    )


class CitationItem(BaseModel):
    source_index: int
    chunk_id: str
    document_id: str
    title: str = ""
    page_number: int | None = None
    score: float = 0.0
    snippet: str = ""


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationItem] = Field(default_factory=list)
    session_id: str | None = None
    is_grounded: bool
    gate_passed: bool
    top_score: float = 0.0
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    abstention_reason: str | None = None
    tokens_used: int = 0
    tokens_remaining: int | None = None


class ChatSessionCreated(BaseModel):
    session_id: str
    session_token: str
    expires_at: datetime
    token_budget: int


class MessageItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: str
    created_at: datetime | None = None


class ConversationHistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageItem]


class WidgetConfigResponse(BaseModel):
    knowledge_base_name: str
    authority_name: str
    contact_url: str
    welcome_message: str
    require_session: bool
    max_query_chars: int
    max_context_turns: int


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
    token_count: int
    metadata_json: dict[str, Any]
    status: str


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str
    storage: str
    queue: str
    llm_model: str
    embedding_model: str
    stale_embedding_chunks: int


class SystemStatsResponse(BaseModel):
    total_documents: int
    documents_by_status: dict[str, int]
    total_chunks: int
    total_sessions: int
    total_messages: int
    active_embedding_model: str


class ReindexResponse(BaseModel):
    queued: int


class EvalRunRequest(BaseModel):
    mode: Literal["retrieval", "answer"]
    judge: bool = Field(
        default=False, description="Answer mode only: add LLM-judge metrics."
    )


class EvalRunQueued(BaseModel):
    id: uuid.UUID
    status: str


class EvalRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    finished_at: datetime | None = None
    mode: str
    status: str
    metrics: dict[str, Any] = Field(
        default_factory=dict, validation_alias="metrics_json"
    )
    error: str | None = None


class EvalRunDetail(EvalRunSummary):
    results: list[dict[str, Any]] = Field(
        default_factory=list, validation_alias="results_json"
    )
