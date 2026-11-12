/**
 * Typed client for the NursingBot API.
 *
 * Same origin only: the session lives in an HttpOnly cookie the browser
 * attaches by itself, and every state-changing request carries the CSRF
 * header the API requires (X-Requested-With: nursingbot).
 */
import type {
  AdminUser,
  AdminUserList,
  AppConfig,
  ChunkItem,
  ConversationDetail,
  ConversationList,
  ConversationSummary,
  DocumentItem,
  DocumentList,
  EvalRunDetail,
  EvalRunSummary,
  FeedbackList,
  GuestSession,
  HealthReport,
  SystemStats,
  TemporaryPassword,
  User,
} from "./types";

export const API_BASE = "/api/v1";
export const CSRF_HEADER = { "X-Requested-With": "nursingbot" } as const;

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function describeDetail(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg, ...}, ...]
    const messages = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item ? String(item.msg) : "",
      )
      .filter(Boolean);
    if (messages.length) return messages.join(" ");
  }
  if (status === 429) return "Too many requests. Please wait a moment.";
  if (status >= 500) return "The service is temporarily unavailable. Please try again.";
  return `Request failed (${status}).`;
}

export async function toApiError(response: Response): Promise<ApiError> {
  let detail: unknown = null;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    // not JSON (a proxy error page, for example)
  }
  const retry = Number(response.headers.get("Retry-After"));
  return new ApiError(
    response.status,
    describeDetail(detail, response.status),
    Number.isFinite(retry) && retry > 0 ? retry : null,
  );
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  json?: unknown;
  body?: BodyInit;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { json, headers: extra, ...init } = options;
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(extra);
  if (method !== "GET" && method !== "HEAD") {
    headers.set("X-Requested-With", CSRF_HEADER["X-Requested-With"]);
  }
  let body = init.body;
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    body,
    credentials: "same-origin",
  });
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  config: () => request<AppConfig>("/app/config"),

  // ---------------------------------------------------------- account
  me: () => request<User>("/auth/me"),
  /** Always 200: `user` is null for a guest. */
  session: () => request<{ user: User | null }>("/auth/session"),
  signup: (body: {
    email: string;
    password: string;
    display_name?: string;
    accept_privacy: boolean;
  }) => request<User>("/auth/signup", { method: "POST", json: body }),
  login: (body: { email: string; password: string }) =>
    request<User>("/auth/login", { method: "POST", json: body }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  logoutAll: () => request<void>("/auth/logout-all", { method: "POST" }),
  changePassword: (body: { current_password: string; new_password: string }) =>
    request<void>("/auth/password/change", { method: "POST", json: body }),
  updateProfile: (body: { display_name: string | null }) =>
    request<User>("/me", { method: "PATCH", json: body }),
  deleteAccount: (password: string) =>
    request<void>("/me", { method: "DELETE", json: { password } }),
  exportData: async (): Promise<Blob> => {
    const response = await fetch(`${API_BASE}/me/export`, { credentials: "same-origin" });
    if (!response.ok) throw await toApiError(response);
    return response.blob();
  },

  // ---------------------------------------------------- conversations
  listConversations: (params: { query?: string; archived?: boolean; cursor?: string | null }) =>
    request<ConversationList>(`/conversations${query({ ...params, limit: 50 })}`),
  createConversation: (title?: string) =>
    request<ConversationSummary>("/conversations", { method: "POST", json: { title } }),
  getConversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  updateConversation: (
    id: string,
    body: { title?: string; pinned?: boolean; archived?: boolean },
  ) => request<ConversationSummary>(`/conversations/${id}`, { method: "PATCH", json: body }),
  deleteConversation: (id: string) =>
    request<void>(`/conversations/${id}`, { method: "DELETE" }),
  sendFeedback: (messageId: string, rating: 1 | -1 | null, comment?: string) =>
    request<void>(`/messages/${messageId}/feedback`, {
      method: "POST",
      json: { rating, comment },
    }),
  importConversations: (conversations: unknown[]) =>
    request<{ imported: number; conversation_ids: string[] }>("/conversations/import", {
      method: "POST",
      json: { conversations },
    }),

  // ------------------------------------------------------------ guest
  createGuestSession: () => request<GuestSession>("/chat/session", { method: "POST" }),

  // ------------------------------------------------------------ admin
  admin: {
    health: () => request<HealthReport>("/admin/health"),
    stats: () => request<SystemStats>("/admin/stats"),
    documents: (params: { status?: string; offset?: number; limit?: number }) =>
      request<DocumentList>(`/documents${query(params)}`),
    uploadDocument: (file: File, docType: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("doc_type", docType || "general");
      return request<DocumentItem>("/documents/upload", { method: "POST", body: form });
    },
    deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),
    reingestDocument: (id: string) =>
      request<DocumentItem>(`/documents/${id}/reingest`, { method: "POST" }),
    chunks: (id: string) => request<ChunkItem[]>(`/documents/${id}/chunks?limit=200`),
    reindexAll: () => request<{ queued: number }>("/admin/reindex", { method: "POST" }),
    evalRuns: () => request<EvalRunSummary[]>("/admin/eval/runs?limit=20"),
    evalRun: (id: string) => request<EvalRunDetail>(`/admin/eval/runs/${id}`),
    startEval: (mode: "retrieval" | "answer", judge: boolean) =>
      request<{ id: string; status: string }>("/admin/eval/runs", {
        method: "POST",
        json: { mode, judge },
      }),
    users: (params: { query?: string; offset?: number }) =>
      request<AdminUserList>(`/admin/users${query({ ...params, limit: 50 })}`),
    updateUser: (id: string, body: { role?: "user" | "admin"; disabled?: boolean }) =>
      request<AdminUser>(`/admin/users/${id}`, { method: "PATCH", json: body }),
    resetPassword: (id: string) =>
      request<TemporaryPassword>(`/admin/users/${id}/reset-password`, { method: "POST" }),
    deleteUser: (id: string) => request<void>(`/admin/users/${id}`, { method: "DELETE" }),
    feedback: (params: { rating?: number | null; offset?: number }) =>
      request<FeedbackList>(`/admin/feedback${query({ ...params, limit: 50 })}`),
  },
};
