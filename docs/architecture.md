# Architecture

NursingBot 2.0 is a standalone chatbot: a React web app and a FastAPI backend on one origin,
behind Caddy. The RAG core of 1.0 (ingestion, hybrid retrieval, relevance gate, citation
verifier, evaluation, tracing, backups) is unchanged. Around it, 2.0 adds user accounts,
saved conversations, an answer router with labelled general answers, and the web app.

## 1. Answering a question

```
question ─► guardrails ─► safety rules ─► condense ─► hybrid retrieval ─► relevance gate
              │              │                                                │
              │ blocked      │ emergency / own health                         ├ passes ─► grounded answer ─► citation check
              ▼              ▼                                                │                                 │ fails
           REFUSED        REFUSED (safety message)                            └ fails ───────────┐              │
                                                                                                 ▼              ▼
                                                                   ROUTER: rules first, then one small LLM classification
                                                                     official_specific ─► DECLINED (+ council contact)
                                                                     personal_medical  ─► REFUSED (safety message)
                                                                     out_of_scope      ─► OUT_OF_SCOPE (scope message)
                                                                     general_nursing   ─► GENERAL answer ─► official-facts check
                                                                                                             fails ─► DECLINED
```

| Step | Code | Notes |
|---|---|---|
| Ask (signed in) | `api/v1/conversations.py` | Stores the question, loads the recent turns and the rolling summary **from the database**, streams SSE, stores the answer with its mode and citations. Stop or disconnect stores the partial answer as `stopped`; its tokens still count. |
| Ask (guest) | `api/v1/chat.py` | Anonymous HMAC-signed session token with a token budget. The browser keeps the history (IndexedDB) and sends the last turns. |
| Guardrails | `generation/guardrails.py`, `api/security.py`, `usage.py` | Length check, injection screen, PII redaction; per-IP and per-session rate limits; per-user daily quota and global daily cap. |
| Safety rules | `generation/router.py` `classify_by_rules` | Emergencies and first-person health questions are refused **before** retrieval, so a matching document can never turn them into an "official" answer. |
| Condense | `generation/condenser.py` | Rewrites a follow-up into a standalone question using the recent turns and the conversation summary. |
| Retrieve + gate | `retrieval/` | pgvector HNSW + PostgreSQL full text, fused with RRF; optional rerank; the gate uses cosine or rerank score, never RRF. |
| Grounded answer | `generation/pipeline.py`, `generation/verifier.py` | Numbered sources, `[Source N]` citations, citation-coverage check. A failed check withdraws the streamed text (`reset` event) and goes to the router. |
| Router | `generation/router.py` | High-precision rules (fees, dates, eligibility, contacts → official; emergencies → medical), then one LLM call returning `{"category": ...}`. Any failure resolves to *declined*. |
| General answer | `generation/pipeline.py` (`GENERAL_SYSTEM_PROMPT`) | Educational answer that must not state official facts. Labelled in the API (`label`) and the UI. `GENERAL_ANSWERS_ENABLED=false` returns to documents-only. |
| Official-facts check | `generation/official_facts.py` | Money amounts, calendar dates, phone numbers, e-mail addresses, web links or "the council requires …" in a general answer → the answer is withdrawn and declined. Clinical numbers (120/80 mmHg, every 2 hours) pass. |
| Memory | `generation/memory.py` | A short title after the first exchange; a rolling summary of older turns every `CONVERSATION_SUMMARY_BATCH` messages (after the response, in the background). |

## 2. Ingestion (worker, asynchronous): unchanged from 1.0

Upload (admin account or API key; size and type checked **before** the body is read) →
object storage → Procrastinate job → parse (PDF, DOCX, TXT, Markdown, CSV, TSV, XLSX, JSON) →
chunk → context headers → embed → index (HNSW + full-text trigger). Stalled jobs are re-queued;
deleted documents stop their jobs.

## 3. Accounts and security

| Concern | Implementation |
|---|---|
| Passwords | Argon2id (`accounts/passwords.py`), minimum 10 characters, common-password list, not the e-mail. |
| Sessions | Random 256-bit token in an `HttpOnly; SameSite=Lax; Path=/api` cookie; only its SHA-256 is stored (`auth_sessions`). Sliding expiry (`AUTH_SESSION_DAYS`). `Secure` follows `AUTH_COOKIE_SECURE`. |
| CSRF | State-changing requests need `X-Requested-With: nursingbot` and a same-site `Origin` (`api/auth.py`). |
| Brute force | Per-IP login rate limit, plus a per-account pause after `LOGIN_MAX_FAILURES` wrong passwords. |
| Forgotten passwords | No e-mail server: an admin issues a one-time temporary password (admin panel or `cli reset-password`). The user is signed out everywhere and must choose a new password. |
| Roles | `user` and `admin`. Admin endpoints accept an admin account (with CSRF) or the `X-Admin-API-Key` for automation. |
| Privacy | Signed-in users export or delete everything from Settings. Admins never see conversations, only anonymised rated answers. Text sent to AI providers and traces is PII-redacted. |
| Edge | Caddy: one strict CSP for the app (`default-src 'self'; frame-ancestors 'none'`), body-size limits, trusted-proxy client IPs, HSTS once on HTTPS. |

## 4. Data (PostgreSQL)

| Table | Holds |
|---|---|
| `documents`, `chunks` | Uploaded files and their searchable pieces (`vector(1536)`, `tsv`). |
| `users`, `auth_sessions` | Accounts and signed-in sessions (migration 004). |
| `conversations`, `messages`, `message_feedback` | Saved chats; each assistant message has `answer_mode`, `citations`, `tokens_used`, `status`. |
| `daily_token_usage` | Tokens per UTC day for each user and for the whole service (quotas). |
| `chat_sessions`, `chat_messages` | Guest sessions (budgets); guest history only if `ENABLE_DATABASE_CHAT_HISTORY`. |
| `eval_runs`, `procrastinate_*` | Evaluation results; the job queue. |

## 5. The web app (`frontend/`)

React 19 + Vite + TypeScript, React Router (declarative mode) and TanStack Query.

| Area | Code |
|---|---|
| Chat | `features/chat/` — one hook (`useChat`) for both guests and signed-in users; streaming via `lib/stream.ts` + `lib/sse.ts`; answers rendered by `lib/markdown.tsx`, which only creates React elements (no HTML strings, no links). |
| Sidebar | `features/sidebar/` — search, date groups, pin, rename, archive, delete. |
| Guests | `features/guest/` — IndexedDB store, session token, import into an account. |
| Accounts | `features/auth/`, `features/settings/` — sign-in, sign-up with consent, forced password change, export, delete, sign out everywhere. |
| Admin | `features/admin/` — overview, documents, evaluation, users (reset, role, disable), feedback. |

## 6. Deployment

`docker-compose.yml`: postgres, minio (+ init), migrate, api, worker, otel-collector, phoenix,
backup, backup-sync, documents-sync, caddy. The `caddy` service is the `nursingbot-web` image
(Node build stage → Caddy with the static app). Both images are tagged `APP_VERSION`, so a
previous release can be started again without a rebuild.

Backups: nightly `pg_dump` (verified, newest 3 always kept) mirrored to object storage, and the
uploaded documents mirrored continuously. `BACKUP_TARGET_URL` sends both off the machine.

## 7. Deliberate differences from the original design

1. **Ragas is not imported** (incompatible with the pinned LangChain); faithfulness and answer relevancy are implemented directly (`evaluation/judge.py`).
2. **LiteLLM runs in-process**, not as a proxy container; pointing `LLM_BASE_URL` at a LiteLLM proxy needs no code change.
3. **Tamil text** is indexed with PostgreSQL's `english` configuration; Tamil questions still work through dense search.
4. **No e-mail**, so accounts are unverified login names and password resets go through an admin. Adding e-mail later is additive (an `email_verified_at` column, a reset-token table).
