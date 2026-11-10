# Architecture

This document maps the original design diagrams to the code. Where the code
deliberately differs from the diagrams, the reason is stated.

## 1. The two pipelines

### Ingestion (worker, asynchronous)

| Step | Code | Notes |
|---|---|---|
| Admin upload | `api/v1/documents.py` `upload_document` | Admin key required. Type check (415), size cap (413), SHA-256 dedup (unique index). Re-uploading a *failed* file retries it. |
| Store + enqueue | `storage/object_store.py`, `jobs.dispatch_ingestion` | Raw file → MinIO/S3, then a Procrastinate job. If the queue is unreachable the document is marked failed with the reason (never stuck in "pending"). |
| Parse | `ingestion/loaders.py`, `ingestion/tabular.py` | Prose: PDF (1-based pages), DOCX (tables in reading order), TXT, Markdown. Tabular: one prose "card" per row (CSV, TSV, XLSX, JSON). Runs in a thread so it never blocks the event loop. |
| Chunk | `ingestion/splitters.py` | Markdown is split on headings first; everything is then split at `CHUNK_SIZE` characters on paragraph, line, sentence and word boundaries. |
| Context headers | `ingestion/context.py` | Always: `[Document] [Section] [Page]` template. Optional (`CONTEXTUAL_HEADERS_ENABLED`): one LLM-written sentence per chunk ("contextual retrieval"). |
| Embed | `models/litellm_client.py` | Batches of `EMBEDDING_BATCH_SIZE`, with timeout and retries. Vectors are fitted to the 1536-dim column (zero-padding keeps cosine similarity exact), so 1024-dim providers (Cohere, Voyage, Jina) work. |
| Index | `ingestion/pipeline.py`, migrations | Old chunks are replaced in one transaction. HNSW (cosine) on `embedding`; `tsv` is filled by a database trigger and GIN-indexed. |

### Generation (API, real time)

| Step | Code | Notes |
|---|---|---|
| Customer question | `api/v1/chat.py`, `web/widget/` | SSE streaming. The widget runs in an iframe and is embedded with one `<script>` tag. |
| Guardrails | `api/security.py`, `api/dependencies.get_chat_session`, `generation/guardrails.py` | Signed session tokens, per-IP rate limits (trusted-proxy aware), per-session token budget, size limits, injection screen, PII redaction. |
| Condense query | `generation/condenser.py` | Rewrites follow-ups using the last turns sent by the client. The rewritten question is used for retrieval **and** for the answer prompt. |
| Embed query | `retrieval/retriever.py` | Same model as ingestion. Dense search only compares chunks embedded with the current model. |
| Hybrid retrieval | `retrieval/dense.py`, `sparse.py`, `fusion.py`, `metadata.py` | pgvector HNSW + PostgreSQL full-text search, fused with RRF. Filters on doc_type, district, program, institution and any metadata key. |
| Rerank (optional) | `retrieval/rerank.py` | `RERANK_ENABLED`, with its own API key and base URL. If the reranker fails, retrieval degrades to fused order. |
| Relevance gate | `retrieval/gate.py` | Gates on rerank score or best cosine similarity, **not** on RRF score (RRF is rank-only and cannot express confidence). Tune with the retrieval eval. |
| Generate answer | `generation/pipeline.py` | Numbered sources, `[Source N]` citations, streamed. `LLM_MAX_TOKENS` caps output. |
| Verify (added) | `generation/verifier.py` | Rejects answers with unknown or missing citations, or citation coverage below `CITATION_MIN_COVERAGE`. Streaming ends with `final_answer`; the widget replaces the draft with it, so a failed answer is retracted on screen. |

## 2. Shared data (PostgreSQL)

| Diagram item | Table(s) |
|---|---|
| documents | `documents` (content_hash unique) |
| chunks + vectors, tsvector | `chunks` (`embedding vector(1536)`, `tsv`, `embedding_model`) |
| jobs queue | `procrastinate_*` (created by `cli migrate`) |
| conversations | `chat_sessions` (token budget, expiry), `chat_messages` (PII-redacted, deleted after `CHAT_HISTORY_RETENTION_DAYS`) |
| eval results | `eval_runs` |

## 3. Shared services

| Diagram box | Implementation |
|---|---|
| LiteLLM gateway | LiteLLM used as a library (`models/litellm_client.py`). Any provider via env vars. Point `LLM_BASE_URL` at a LiteLLM **proxy server** to add central budgets, fallbacks and caching without code changes. |
| Observability | App → **OTel Collector** (`otel-collector-config.yaml`) → **Phoenix**. Spans for HTTP requests, SQL, LLM/embedding calls and RAG stages (`retrieval.hybrid`, `rag.condense`, `rag.generate`, `ingestion.document`), PII-redacted before export. API and worker both trace. |
| Evaluation | `evaluation/` + `cli eval` + admin panel. Retrieval hit rate/MRR/threshold suggestion (no LLM cost), answer checks, and LLM-judge **faithfulness** and **answer relevancy** (the Ragas metrics; see STACK.md for why Ragas itself is not imported). CI gate: `scripts/eval-gate.sh` / `.github/workflows/eval-gate.yml`. |

## 4. Deployment and security (diagram 2)

| Diagram item | Implementation |
|---|---|
| Docker Compose, 1 VM | `docker-compose.yml`: postgres, minio, migrate, api, worker, otel-collector, phoenix, backup, backup-sync, caddy. |
| Nightly pg_dump | `backup` service (`scripts/backup.sh --loop`), 7-day local retention. |
| Backups in object storage | `backup-sync` mirrors the backup volume into the `nursing-backups` bucket. |
| Tested restore | `scripts/restore-test.sh` restores the newest dump into a scratch database and checks it. |
| CI eval gate | `.github/workflows/ci.yml` (lint, types, tests, audit, image) + `eval-gate.yml` (self-hosted runner on the VM). |
| Admin: JWT or API key | API key (`X-Admin-API-Key`, constant-time compare, ≥ 24 chars enforced in production). |
| Chat: session tokens | HMAC-signed, expiring tokens from `POST /chat/session`. |
| Rate + token caps | Per-IP request limits, per-IP session creation limit, per-session LLM token budget, `LLM_MAX_TOKENS`. |
| Secrets manager | Every setting can be a Docker secret file in `/run/secrets/<NAME>`; secrets are `SecretStr` and never logged. |
| Injection guards | Input screen + history sanitising + grounded-only answers + citation verification + strict CSP. |

## 5. Deliberate differences from the diagrams

1. **Chat history is client-side first.** The widget sends its last turns; the server stores a redacted copy only for review and deletes it after the retention period. This keeps the server stateless for answering and limits personal data.
2. **Ragas is not imported** (incompatible with the pinned LangChain); its two core metrics are implemented directly.
3. **LiteLLM runs in-process**, not as a separate proxy container. Adding the proxy later is a configuration change.
4. **Tamil text** is indexed with PostgreSQL's `english` configuration (there is no Tamil one). Tamil questions still work through dense (semantic) search, and the gate no longer requires a keyword match.
