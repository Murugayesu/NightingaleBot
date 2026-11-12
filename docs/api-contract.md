# NursingBot HTTP API contract (2.0)

All endpoints live under `/api/v1` except `/health`. JSON everywhere unless noted. The web app is
served from the same origin (Caddy: `/` = app, `/api/*` = API) and calls the API with relative URLs.

**Errors** look like `{"detail": "<message>"}` (422 validation errors from FastAPI use a list of
`{loc, msg}` objects in `detail`). Rate limits and quotas answer `429` (sometimes with
`Retry-After`); the service-wide daily cap and AI-provider outages answer `503`.

**Authentication**

| Caller | How |
|---|---|
| Signed-in user | `nb_session` cookie (`HttpOnly; SameSite=Lax; Path=/api`; `Secure` when `AUTH_COOKIE_SECURE`), set by sign-up / sign-in. |
| Guest | `X-Session-Token` header from `POST /chat/session`. |
| Admin | A signed-in account with role `admin`, **or** the `X-Admin-API-Key` header (automation, CLI). |

**CSRF:** every `POST`/`PATCH`/`DELETE` authenticated by the cookie must send
`X-Requested-With: nursingbot`; if the browser sends `Origin`, it must be this site. Otherwise `403`.

**Common objects**

```jsonc
// citation
{ "source_index": 1, "chunk_id": "…", "document_id": "…", "title": "TNNMC - Online Registration FAQs.pdf",
  "page_number": 1, "score": 0.61, "snippet": "first ~300 characters of the source" }

// user
{ "id": "uuid", "email": "priya@example.org", "display_name": "Priya", "role": "user|admin",
  "must_change_password": false, "created_at": "…" }

// stored message
{ "id": "uuid", "role": "user|assistant", "content": "…",
  "answer_mode": "grounded|general|declined|refused|out_of_scope|null", "label": "From official records|…|null",
  "citations": [ <citation> ], "status": "complete|stopped|error", "is_imported": false,
  "tokens_used": 812, "created_at": "…", "feedback": 1 | -1 | null }
```

**Answer modes** (`answer_mode`, with the `label` the app shows):

| Mode | Meaning | Label |
|---|---|---|
| `grounded` | From the official documents, cited and verified | From official records |
| `general` | General nursing knowledge; never official facts; no citations | `GENERAL_ANSWER_LABEL` (e.g. "General nursing knowledge: not from official TNNMC records. Verify important details.") |
| `declined` | An official question the documents do not answer | Not in the official records |
| `refused` | Personal medical advice, an emergency, or a blocked request | Can't help with this |
| `out_of_scope` | Not about nursing | Outside what I can help with |

---

## 1. Public

### `GET /health` → `{ "status": "ok", "version": "2.0.0" }`

### `GET /api/v1/app/config`
```json
{ "knowledge_base_name": "TNNMC Nursing Knowledge Base", "authority_name": "Tamil Nadu Nurses and Midwives Council",
  "authority_short_name": "TNNMC", "contact_url": "https://…/contact", "welcome_message": "…",
  "sign_in_methods": ["password"], "signup_enabled": true, "guest_chat_enabled": true,
  "general_answers_enabled": true, "general_answer_label": "…", "password_min_length": 10,
  "require_session": true, "max_query_chars": 4000, "max_context_turns": 3,
  "guest_token_budget": 50000, "user_daily_token_quota": 200000 }
```

---

## 2. Accounts

| Endpoint | Body | Result |
|---|---|---|
| `POST /auth/signup` | `{ email, password, display_name?, accept_privacy: true }` | `201` user + session cookie. `409` e-mail taken, `422` weak password / no consent / bad e-mail, `403` sign-ups closed. Rate-limited per IP. |
| `POST /auth/login` | `{ email, password }` | `200` user + cookie. `401` wrong e-mail or password (same message for both), `403` disabled, `429` too many attempts (per IP, or the account is paused; `Retry-After`). |
| `POST /auth/logout` | — | `204`, cookie cleared. |
| `POST /auth/logout-all` | — | `204`: every session of the user ends, including this one. |
| `GET /auth/session` | — | Always `200`: `{ "user": <user> \| null }`. |
| `GET /auth/me` | — | `200` user, or `401` when signed out. |
| `POST /auth/password/change` | `{ current_password, new_password }` | `204`. Other sessions end; this one stays. Also completes an admin reset (`must_change_password` → false). |
| `PATCH /me` | `{ display_name }` | `200` user. |
| `GET /me/export` | — | `200` JSON file (`Content-Disposition: attachment`): account, conversations with messages, feedback. |
| `DELETE /me` | `{ password }` | `204`: account, chats and feedback deleted; cookie cleared. |

While `must_change_password` is true, every other user endpoint answers `403`
("Choose a new password before continuing.").

---

## 3. Conversations (signed-in users)

### `GET /conversations?query=&archived=false&cursor=&limit=30`
Pinned first, then most recently active. `query` searches titles and message text.
```json
{ "conversations": [ { "id": "uuid", "title": "TNNMC registration fee", "pinned": false, "archived": false,
                       "created_at": "…", "updated_at": "…" } ],
  "next_cursor": "opaque string | null" }
```

### `POST /conversations` `{ "title"?: "…" }` → `201` conversation summary (title defaults to "New chat").
### `GET /conversations/{id}` → summary + `"messages": [ <stored message> ]` (oldest first). `404` if it is not yours.
### `PATCH /conversations/{id}` `{ "title"?, "pinned"?, "archived"? }` → `200` summary.
### `DELETE /conversations/{id}` → `204`.

### `POST /conversations/{id}/messages` `{ "content": "…", "stream": true, "filters"?: {…} }`
Asks a question. The server stores it, loads the conversation's history itself, answers, and stores
the answer. `429` when the daily quota is used up, `503` when the service-wide cap is reached.

**Streaming** (`stream: true`, `Content-Type: text/event-stream`). Read it with `fetch()` and a
stream reader (EventSource cannot POST). Events in order:

| event | data | meaning |
|---|---|---|
| `start` | `{ "conversation_id", "user_message": <stored message> }` | The question was stored. |
| `mode` | `{ "answer_mode", "label" }` | How the text that follows is being produced. Show the label at once (general answers are labelled while they stream). |
| `citations` | `[ <citation> ]` | Sources for a grounded attempt. |
| `token` | `"text"` | Append to the answer. |
| `reset` | `{}` | **Withdraw everything streamed so far** (the grounded attempt failed its checks). A new `mode` and new tokens follow. |
| `end` | `{ "final_answer", "answer_mode", "label", "citations", "is_grounded", "gate_passed", "top_score", "abstention_reason", "tokens_used", "message": <stored message>, "quota_remaining" }` | **Replace the displayed text with `final_answer`.** |
| `title` | `{ "conversation_id", "title" }` | Sent after the first answer of a new chat. |
| `error` | `{ "detail" }` | Generation failed; the partial answer is stored with status `error`. |

If the client disconnects (Stop), the partial answer is stored with status `stopped` and its tokens
are charged.

**Non-streaming** (`stream: false`): `200`
`{ "user_message": <stored message>, "assistant_message": <stored message>, "title": "…|null", "quota_remaining": 199188 }`.

### `POST /messages/{id}/regenerate?stream=true`
`id` must be the conversation's latest message: an answer (it is replaced) or a question whose
answer never arrived. Same responses as asking. `409` for an older message.

### `POST /messages/{id}/feedback` `{ "rating": 1 | -1 | null, "comment"?: "…" }` → `204`
`null` removes your rating. Only answers can be rated.

### `POST /conversations/import`
Copies a guest's browser-kept chats into the account (at most 50 per request, 200 messages each).
```json
{ "conversations": [ { "title": "…", "created_at": "…", "messages": [
    { "role": "user|assistant", "content": "…", "answer_mode": "grounded|…|null",
      "citations": [ <citation> ], "created_at": "…" } ] } ] }
```
→ `201` `{ "imported": 1, "conversation_ids": ["uuid"] }`. Imported messages have `is_imported: true`
(shown as they were, not re-checked); unknown answer modes are dropped.

---

## 4. Guest chat

### `POST /chat/session` → `201`
`{ "session_id", "session_token", "expires_at", "token_budget" }`. Rate-limited per IP. `403` when guest chat is off.

### `POST /chat`
Header `X-Session-Token`. Body:
```json
{ "query": "How much is the registration fee?", "stream": true,
  "recent_context": [ {"role": "user", "content": "…"}, {"role": "assistant", "content": "…"} ],
  "filters": null }
```
`recent_context`: the last `max_context_turns` user/assistant pairs, oldest first, each `content` ≤ 2000 characters.

Streaming events: `mode`, `citations`, `token`, `reset`, `end`, `error` as above. `end` carries
`tokens_remaining` (the session budget) instead of `message` / `quota_remaining`.
Non-streaming: `{ "answer", "answer_mode", "label", "citations", "session_id", "is_grounded",
"gate_passed", "top_score", "applied_filters", "abstention_reason", "tokens_used", "tokens_remaining" }`.

`401` invalid or expired token: create a new session and retry **once**. `429` rate limit or
session budget used up (`detail` says which).

### `GET /chat/sessions/{session_id}` (header `X-Session-Token` of that session)
`{ "session_id", "messages": [ { "role", "content", "created_at" } ] }`, empty unless
`ENABLE_DATABASE_CHAT_HISTORY` is on.

---

## 5. Admin

An admin account (cookie + CSRF header) or `X-Admin-API-Key`. Otherwise `401` (not signed in) or `403` (not an admin).

### System
- `GET /admin/health` → `{ "status": "ok|degraded", "environment", "database", "storage", "queue", "llm_model", "embedding_model", "stale_embedding_chunks" }` (always `200`; read `status`).
- `GET /admin/stats` → `{ "total_documents", "documents_by_status", "total_chunks", "total_sessions", "total_messages", "total_users", "total_conversations", "answers_last_7_days": {"grounded": 120, "general": 40, …}, "tokens_today", "active_embedding_model" }`.
- `POST /admin/reindex` → `202` `{ "queued": 12 }` (re-ingests every document).

### Documents
- `GET /documents?doc_type=&status=&limit=20&offset=0` (`limit` 1–100) → `{ "documents": [ <document> ], "total" }`
  where a document is `{ id, filename, doc_type, content_type: "prose|tabular", s3_key, status: "pending|processing|completed|failed", chunk_count, error_message, metadata_json, created_at, updated_at }`.
- `GET /documents/{id}` → document.
- `POST /documents/upload` (multipart: `file`, `doc_type`, optional `metadata_json` object) → `201` new, `200` identical file already present, `400` empty / bad metadata, `401` not an admin (checked **before** the body is read), `413` too large, `415` unsupported type. Types: `.pdf .docx .txt .md .markdown .csv .tsv .xlsx .json`.
- `POST /documents/{id}/reingest` → `202` document. `DELETE /documents/{id}` → `204`.
- `GET /documents/{id}/chunks?limit=200&offset=0` → `[ { id, document_id, chunk_index, content, heading_hierarchy, page_number, token_count, metadata_json, status } ]`.

### Evaluation
- `POST /admin/eval/runs` `{ "mode": "retrieval" | "answer", "judge": false }` → `202` `{ "id", "status": "queued" }`.
- `GET /admin/eval/runs?limit=20` → `[ { id, created_at, finished_at, mode, status: "queued|running|completed|failed", metrics, error } ]`.
- `GET /admin/eval/runs/{id}` → the same plus `results: [ { case_id, category, question, expected_behavior, passed, detail, answer_mode?, … } ]`.

### Users
- `GET /admin/users?query=&offset=0&limit=50` → `{ "users": [ { id, email, display_name, role, must_change_password, disabled, created_at, last_login_at, conversations } ], "total" }`.
- `PATCH /admin/users/{id}` `{ "role"?: "user|admin", "disabled"?: bool }` → user item. Disabling signs the account out. You cannot demote or disable yourself, and the last active admin cannot be removed (`400`).
- `POST /admin/users/{id}/reset-password` → `{ "email", "temporary_password", "note" }`: shown once; the user is signed out everywhere and must choose a new password.
- `DELETE /admin/users/{id}` → `204` (the account and all its chats).

### Feedback (anonymised)
- `GET /admin/feedback?rating=-1&offset=0&limit=50` → `{ "items": [ { message_id, rating, comment, question, answer, answer_mode, created_at } ], "total" }`. No user identities.
