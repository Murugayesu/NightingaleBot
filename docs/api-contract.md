# NursingBot HTTP API contract

All endpoints live under `/api/v1` except `/health`. JSON everywhere unless noted.
The web UI is served by the API itself, so the browser calls the API **same-origin** with relative URLs:

| Path | What |
|---|---|
| `/ui/widget/chat.html` | Chat widget page (runs inside an iframe) |
| `/ui/widget/embed.js` | One-line embed script for the council's website |
| `/ui/admin/` | Admin panel |

Errors always look like `{"detail": "<message>"}` (422 validation errors use FastAPI's list format in `detail`).

---

## 1. Public endpoints (chat widget)

### `GET /api/v1/widget/config`
```json
{
  "knowledge_base_name": "TNNMC Nursing Knowledge Base",
  "authority_name": "Tamil Nadu Nurses and Midwives Council",
  "contact_url": "https://www.tamilnadunursingcouncil.com/contact",
  "welcome_message": "Ask me about registration, renewal, admissions ...",
  "require_session": true,
  "max_query_chars": 4000,
  "max_context_turns": 2
}
```

### `POST /api/v1/chat/session`
Creates an anonymous chat session. No body. Rate-limited per IP.
`201`:
```json
{ "session_id": "3f1c…", "session_token": "<opaque string>", "expires_at": "2026-11-07T10:00:00Z", "token_budget": 50000 }
```

### `POST /api/v1/chat`
Headers: `X-Session-Token: <session_token>` (required when `require_session` is true).
Body:
```json
{
  "query": "How much is the registration fee?",
  "stream": true,
  "recent_context": [
    {"role": "user", "content": "previous question"},
    {"role": "assistant", "content": "previous answer"}
  ],
  "filters": null
}
```
`recent_context` = the last `max_context_turns` user/assistant pairs (so at most `2 * max_context_turns` messages), oldest first. Each `content` ≤ 2000 chars.

**Citation object** (used below):
```json
{ "source_index": 1, "chunk_id": "…", "document_id": "…", "title": "TNNMC - Online Registration FAQs.pdf",
  "page_number": 1, "score": 0.61, "snippet": "first ~300 chars of the source chunk" }
```

**Non-streaming (`stream: false`)**, `200`:
```json
{
  "answer": "The fee is Rs. 365 [Source 1].",
  "citations": [ <citation>, … ],
  "session_id": "3f1c…",
  "is_grounded": true,
  "gate_passed": true,
  "top_score": 0.61,
  "applied_filters": {},
  "abstention_reason": null,
  "tokens_used": 812,
  "tokens_remaining": 49188
}
```

**Streaming (`stream: true`)**, `200`, `Content-Type: text/event-stream`. The body is Server-Sent Events. Because it is a POST, the browser must read it with `fetch()` plus a `ReadableStream` parser, not `EventSource`. Events, in order:

| event | data (JSON) | meaning |
|---|---|---|
| `citations` | `[<citation>, …]` | Sources found. May be absent when the bot abstains. |
| `token` | `"text piece"` (a JSON string) | Append to the displayed answer. |
| `end` | `{ "final_answer": "…", "citations": […], "is_grounded": bool, "gate_passed": bool, "abstention_reason": str\|null, "applied_filters": {}, "tokens_used": int, "tokens_remaining": int }` | **The client MUST replace the displayed text with `final_answer`.** If the streamed answer failed citation verification, `final_answer` is the abstention message, and the streamed draft must disappear. |
| `error` | `{ "detail": "…" }` | Generation failed mid-stream. Show a friendly error. |

**Error statuses:**
- `401` invalid or expired session token. The client should create a new session and retry **once**.
- `429` rate limit or session token budget exhausted (`detail` explains which).
- `422` validation error (e.g. query too long).

### `GET /api/v1/chat/sessions/{session_id}`
Header `X-Session-Token` (must belong to that session). `200`:
```json
{ "session_id": "…", "messages": [ { "role": "user", "content": "…", "created_at": "…" } ] }
```
Returns an empty list when server-side history is disabled.

---

## 2. Admin endpoints

All require header `X-Admin-API-Key: <key>`. A wrong or missing key gives `401`.

### `GET /api/v1/admin/health`
```json
{ "status": "ok|degraded", "environment": "production", "database": "ok", "storage": "ok", "queue": "ok|disabled|unhealthy: …",
  "llm_model": "openai/gpt-4o-mini", "embedding_model": "openai/text-embedding-3-small", "stale_embedding_chunks": 0 }
```

### `GET /api/v1/admin/stats`
```json
{ "total_documents": 12, "documents_by_status": {"completed": 10, "failed": 1, "pending": 1},
  "total_chunks": 840, "total_sessions": 51, "total_messages": 230, "active_embedding_model": "openai/text-embedding-3-small" }
```

### `GET /api/v1/documents?doc_type=&status=&limit=20&offset=0`
`limit` is 1–100.
```json
{ "documents": [ <document>, … ], "total": 12 }
```
**Document object:**
```json
{ "id": "uuid", "filename": "x.pdf", "doc_type": "general", "content_type": "prose|tabular", "s3_key": "…",
  "status": "pending|processing|completed|failed", "chunk_count": 42, "error_message": null,
  "metadata_json": {}, "created_at": "…", "updated_at": "…" }
```

### `GET /api/v1/documents/{id}` → document object, `404` if missing.

### `POST /api/v1/documents/upload` (multipart/form-data)
Fields: `file` (required), `doc_type` (string, default `general`), `metadata_json` (optional string containing a JSON **object**).
Supported extensions: `.pdf .docx .txt .md .markdown .csv .tsv .xlsx .json`.
- `201` + document object: new document accepted (status `pending` in queue mode, `completed` or `failed` in inline mode).
- `200` + document object: identical file already exists (duplicate). The UI should say "already uploaded".
- `400` empty file or bad `metadata_json`; `413` too large; `415` unsupported type.

### `POST /api/v1/documents/{id}/reingest` → `202` + document object (status reset to `pending`).
### `DELETE /api/v1/documents/{id}` → `204`.
### `GET /api/v1/documents/{id}/chunks?limit=200&offset=0`
```json
[ { "id": "…", "document_id": "…", "chunk_index": 0, "content": "…", "heading_hierarchy": ["…"],
    "page_number": 1, "token_count": 120, "metadata_json": {}, "status": "active" } ]
```

### `POST /api/v1/admin/reindex` → `202` `{ "queued": 12 }`
Re-ingests every document. Use after changing the embedding model.

### `POST /api/v1/admin/eval/runs`
Body `{ "mode": "retrieval" | "answer" }` → `202` `{ "id": "uuid", "status": "queued" }`.

### `GET /api/v1/admin/eval/runs?limit=20`
```json
[ { "id": "…", "created_at": "…", "finished_at": "…|null", "mode": "retrieval", "status": "queued|running|completed|failed",
    "metrics": { "hit_rate": 0.82, "…": 0 }, "error": null } ]
```

### `GET /api/v1/admin/eval/runs/{id}`
Same as above, plus `"results": [ { "case_id": "reg-001", "category": "registration", "question": "…", "passed": true, "detail": "…" } ]`.

---

## 3. Public system endpoint
`GET /health` → `{ "status": "ok", "version": "x.y.z" }`
