/** Shapes returned by the NursingBot API (see docs/api-contract.md). */

export type AnswerMode =
  | "grounded"
  | "general"
  | "declined"
  | "refused"
  | "out_of_scope";

export type Role = "user" | "assistant";
export type MessageStatus = "complete" | "stopped" | "error";

export interface Citation {
  source_index: number;
  chunk_id: string;
  document_id: string;
  title: string;
  page_number: number | null;
  score: number;
  snippet: string;
}

export interface AppConfig {
  knowledge_base_name: string;
  authority_name: string;
  authority_short_name: string;
  contact_url: string;
  welcome_message: string;
  sign_in_methods: string[];
  signup_enabled: boolean;
  guest_chat_enabled: boolean;
  general_answers_enabled: boolean;
  general_answer_label: string;
  password_min_length: number;
  require_session: boolean;
  max_query_chars: number;
  max_context_turns: number;
  guest_token_budget: number;
  user_daily_token_quota: number;
}

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  role: "user" | "admin";
  must_change_password: boolean;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  pinned: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConversationList {
  conversations: ConversationSummary[];
  next_cursor: string | null;
}

export interface StoredMessage {
  id: string;
  role: Role;
  content: string;
  answer_mode: AnswerMode | null;
  label: string | null;
  citations: Citation[];
  status: MessageStatus;
  is_imported: boolean;
  tokens_used: number;
  created_at: string;
  feedback: 1 | -1 | null;
}

export interface ConversationDetail extends ConversationSummary {
  messages: StoredMessage[];
}

export interface GuestSession {
  session_id: string;
  session_token: string;
  expires_at: string;
  token_budget: number;
}

/** The `end` event of an answer stream. */
export interface StreamEnd {
  final_answer: string;
  answer_mode: AnswerMode;
  label: string;
  citations: Citation[];
  is_grounded: boolean;
  gate_passed: boolean;
  tokens_used: number;
  message?: StoredMessage | null;
  quota_remaining?: number | null;
  tokens_remaining?: number | null;
}

// ------------------------------------------------------------------ admin
export interface HealthReport {
  status: string;
  environment: string;
  database: string;
  storage: string;
  queue: string;
  llm_model: string;
  embedding_model: string;
  stale_embedding_chunks: number;
}

export interface SystemStats {
  total_documents: number;
  documents_by_status: Record<string, number>;
  total_chunks: number;
  total_sessions: number;
  total_messages: number;
  total_users: number;
  total_conversations: number;
  answers_last_7_days: Record<string, number>;
  tokens_today: number;
  active_embedding_model: string;
}

export interface DocumentItem {
  id: string;
  filename: string;
  doc_type: string;
  content_type: string;
  status: "pending" | "processing" | "completed" | "failed";
  chunk_count: number;
  error_message: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
}

export interface DocumentList {
  documents: DocumentItem[];
  total: number;
}

export interface ChunkItem {
  id: string;
  chunk_index: number;
  content: string;
  heading_hierarchy: string[];
  page_number: number | null;
  token_count: number;
}

export interface EvalRunSummary {
  id: string;
  created_at: string;
  finished_at: string | null;
  mode: "retrieval" | "answer";
  status: string;
  metrics: Record<string, unknown>;
  error: string | null;
}

export interface EvalRunDetail extends EvalRunSummary {
  results: Array<Record<string, unknown>>;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  role: "user" | "admin";
  must_change_password: boolean;
  disabled: boolean;
  created_at: string;
  last_login_at: string | null;
  conversations: number;
}

export interface AdminUserList {
  users: AdminUser[];
  total: number;
}

export interface TemporaryPassword {
  email: string;
  temporary_password: string;
  note: string;
}

export interface FeedbackItem {
  message_id: string;
  rating: 1 | -1;
  comment: string | null;
  question: string;
  answer: string;
  answer_mode: AnswerMode | null;
  created_at: string;
}

export interface FeedbackList {
  items: FeedbackItem[];
  total: number;
}
