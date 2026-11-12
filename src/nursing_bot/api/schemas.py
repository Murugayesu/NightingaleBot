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
    answer_mode: str
    label: str
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


class AppConfigResponse(BaseModel):
    knowledge_base_name: str
    authority_name: str
    authority_short_name: str
    contact_url: str
    welcome_message: str
    sign_in_methods: list[str]
    signup_enabled: bool
    guest_chat_enabled: bool
    general_answers_enabled: bool
    general_answer_label: str
    password_min_length: int
    require_session: bool
    max_query_chars: int
    max_context_turns: int
    guest_token_budget: int
    user_daily_token_quota: int


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
    total_sessions: int  # guest sessions
    total_messages: int  # stored guest messages
    total_users: int
    total_conversations: int
    answers_last_7_days: dict[str, int]  # by answer mode
    tokens_today: int
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


# ------------------------------------------------------------------ accounts
class SignupRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=256)
    display_name: str | None = Field(default=None, max_length=80)
    accept_privacy: bool = Field(
        default=False, description="The user accepted the privacy notice."
    )


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., max_length=256)
    new_password: str = Field(..., max_length=256)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None = None
    role: str
    must_change_password: bool = False
    created_at: datetime


class SessionResponse(BaseModel):
    user: UserResponse | None = None


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)


class DeleteAccountRequest(BaseModel):
    password: str = Field(..., max_length=256)


# ------------------------------------------------------------- conversations
class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    pinned: bool
    archived: bool
    created_at: datetime
    updated_at: datetime


class ConversationList(BaseModel):
    conversations: list[ConversationSummary]
    next_cursor: str | None = None


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None


class StoredMessage(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    answer_mode: str | None = None
    label: str | None = None
    citations: list[CitationItem] = Field(default_factory=list)
    status: str
    is_imported: bool = False
    tokens_used: int = 0
    created_at: datetime
    feedback: int | None = None  # this user's rating: 1, -1 or none


class ConversationDetail(ConversationSummary):
    messages: list[StoredMessage]


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_QUERY_LENGTH)
    stream: bool = True
    filters: FilterPayload | None = None


class SendMessageResponse(BaseModel):
    user_message: StoredMessage
    assistant_message: StoredMessage
    title: str | None = None
    quota_remaining: int | None = None


class FeedbackRequest(BaseModel):
    rating: Literal[-1, 1] | None = Field(
        ..., description="1 = helpful, -1 = not helpful, null = remove my rating"
    )
    comment: str | None = Field(default=None, max_length=1000)


class ImportCitation(BaseModel):
    source_index: int = 0
    title: str = Field(default="", max_length=512)
    page_number: int | None = None
    snippet: str = Field(default="", max_length=2000)
    document_id: str = Field(default="", max_length=64)
    chunk_id: str = Field(default="", max_length=64)
    score: float = 0.0


class ImportMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=20_000)
    answer_mode: str | None = Field(default=None, max_length=16)
    citations: list[ImportCitation] = Field(default_factory=list, max_length=20)
    created_at: datetime | None = None


class ImportConversation(BaseModel):
    title: str = Field(default="Imported chat", max_length=200)
    created_at: datetime | None = None
    messages: list[ImportMessage] = Field(..., min_length=1, max_length=200)


class ImportRequest(BaseModel):
    conversations: list[ImportConversation] = Field(..., min_length=1, max_length=50)


class ImportResult(BaseModel):
    imported: int
    conversation_ids: list[uuid.UUID]


# ------------------------------------------------------------- admin: users
class AdminUserItem(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None = None
    role: str
    must_change_password: bool
    disabled: bool
    created_at: datetime
    last_login_at: datetime | None = None
    conversations: int = 0


class AdminUserList(BaseModel):
    users: list[AdminUserItem]
    total: int


class AdminUserUpdate(BaseModel):
    role: Literal["user", "admin"] | None = None
    disabled: bool | None = None


class TemporaryPasswordResponse(BaseModel):
    email: str
    temporary_password: str
    note: str = (
        "Give this password to the user privately. It works once: they must "
        "choose a new password when they sign in. It is not shown again."
    )


class FeedbackItem(BaseModel):
    """Anonymised: no user identity, only the rated exchange."""

    message_id: uuid.UUID
    rating: int
    comment: str | None = None
    question: str
    answer: str
    answer_mode: str | None = None
    created_at: datetime


class FeedbackList(BaseModel):
    items: list[FeedbackItem]
    total: int
