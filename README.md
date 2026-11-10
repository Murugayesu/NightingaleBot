# NursingBot

A question-answering assistant for the **Tamil Nadu Nurses and Midwives Council (TNNMC)**.
It answers only from official council documents, cites every fact, and says so
when the documents don't contain the answer.

```
 council website ──► chat widget (iframe) ─┐
 admin panel ──────────────────────────────┼──► Caddy (TLS) ──► FastAPI ──► PostgreSQL + pgvector
                                           │                       │  └─────► MinIO / S3 (raw files)
                                           │                       └─ jobs ─► Procrastinate worker
                                           │                    traces ─────► OTel Collector ──► Phoenix
```

- **Ingestion:** upload → parse (PDF, DOCX, TXT, Markdown, CSV, TSV, XLSX, JSON) → chunk → context headers → embed → index (HNSW + full text).
- **Answering:** guardrails → follow-up rewriting → hybrid search (vectors + keywords, RRF) → optional rerank → relevance gate → cited answer → citation check.
- **Evaluation:** 57 hand-verified questions (`eval/evalset.csv`): retrieval hit rate, answer checks, and LLM-judge faithfulness and relevancy.

Read `docs/architecture.md` for how it fits together and `docs/operations.md` to run it in production.
The HTTP API is specified in `docs/api-contract.md`.

---

## Quick start (Docker)

```bash
cp .env.example .env
docker compose run --rm --no-deps migrate python -m nursing_bot.cli gen-secrets   # paste into .env
# also set LLM_API_KEY (and EMBEDDING_API_KEY if different) in .env
docker compose up -d --build
```

| URL | What |
|---|---|
| http://localhost/ui/widget/chat.html | Chat widget |
| http://localhost/ui/admin/ | Admin panel (log in with `ADMIN_API_KEY`) |
| http://localhost:6006 | Phoenix traces (bound to localhost only) |

Load the council documents in bulk:

```bash
docker compose run --rm -v "$PWD/../corpus:/corpus:ro" migrate \
  python -m nursing_bot.cli ingest-dir /corpus --recursive
```

Embed the widget in any web page (add the page's origin to `WIDGET_FRAME_ANCESTORS`):

```html
<script src="https://YOUR-DOMAIN/ui/widget/embed.js" async></script>
```

## Local development (without Docker for the app)

```bash
uv sync --extra dev --extra parsers
docker compose up -d postgres minio minio-init         # infrastructure only
uv run python -m nursing_bot.cli migrate
uv run uvicorn nursing_bot.api.main:app --reload        # INGESTION_MODE=inline: no worker needed
```

## Quality checks

```bash
uv run ruff check src tests alembic && uv run ruff format --check src tests alembic
uv run mypy
# integration tests need a disposable pgvector database (it is wiped!):
docker run -d --name nursingbot-test-pg -e POSTGRES_PASSWORD=testpass -e POSTGRES_DB=nursing_bot_test \
  -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.7-pg18-trixie
uv run pytest --cov
```

Evaluation (after ingesting the corpus):

```bash
uv run python -m nursing_bot.cli eval retrieval            # free: hit rate + suggested gate threshold
uv run python -m nursing_bot.cli eval answer --judge       # LLM cost: answers + faithfulness
```

## Project layout

```
src/nursing_bot/
  config.py           every setting, with production safety checks
  privacy.py          PII redaction (shared by guardrails, history, tracing)
  services.py         LLM client + object storage singletons
  jobs.py             background jobs (ingest, eval, retention) and dispatch
  cli.py              migrate | ingest-dir | eval | gen-secrets
  api/                FastAPI app, routes, auth, schemas
  ingestion/          loaders, splitter, context headers, tabular cards, pipeline
  retrieval/          dense, sparse, fusion, rerank, gate, retriever
  generation/         guardrails, condenser, assembler, verifier, pipeline
  evaluation/         dataset, metrics, LLM judge, runner
  models/             LiteLLM client (the only module that calls AI providers)
  storage/            ORM models, database, S3
  observability/      logging, OpenTelemetry → Phoenix
  workers/            Procrastinate tasks
  web/                chat widget + admin panel (plain HTML/CSS/JS)
alembic/              database migrations
eval/                 evaluation set
scripts/              backup, restore, restore test, eval gate
```
