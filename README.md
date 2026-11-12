# NursingBot

A standalone chatbot for nurses, nursing students and applicants in Tamil Nadu.
It answers from the official documents of the **Tamil Nadu Nurses and Midwives Council (TNNMC)**
with citations, answers general nursing questions from general knowledge with a clear label, and
never invents official facts.

```
 browser ──► http://<server IP>  (HTTPS once a domain exists)
               │
             Caddy ─┬─ /        React app (static files)
                    └─ /api/*  FastAPI ──► PostgreSQL + pgvector (documents, chunks, users, chats)
                                  │   └──► MinIO / S3 (original files)
                                  └ jobs ► Procrastinate worker (ingestion, evaluation)
                         traces ────────► OTel Collector ──► Phoenix
```

**Every answer says how it was produced:**

| Badge | When | What the user sees |
|---|---|---|
| From official records | The council's documents answer the question | A cited answer; the sources open with page and excerpt |
| General nursing knowledge | A general nursing question the documents don't cover | A labelled general answer: no fees, dates, contacts or rules |
| Not in the official records | An official question the documents can't answer | A clear "not found" with the council's contact link |
| Safety notice | Personal medical questions and emergencies | Advice to see a doctor; 108 in an emergency |
| Outside my scope | Anything not about nursing | A polite scope message |

**Two ways to use it:**
- **Guests** chat at once. Their chats stay in their browser.
- **Signed-in users** (e-mail + password) get their chats saved on the server, searchable, and available on any device. Guest chats can be brought along at sign-up.

**Admins** (an account with the admin role) upload documents, run evaluations, review anonymised
feedback and manage accounts, including resetting forgotten passwords. There is no e-mail server.

Read `docs/architecture.md` for how it fits together, `docs/operations.md` to run it, and
`docs/api-contract.md` for the HTTP API.

---

## Quick start (Docker)

```bash
cp .env.example .env
docker compose run --rm --no-deps migrate python -m nursing_bot.cli gen-secrets   # paste into .env
# set LLM_API_KEY (and EMBEDDING_API_KEY if different); pin LLM_MODEL to one model
docker compose up -d --build
docker compose exec api python -m nursing_bot.cli create-user you@example.org --admin
```

The last command prints a one-time password. Open **http://localhost** (or `http://<server IP>`),
sign in with it, choose your own password, then upload documents under **Admin → Documents**.
For a folder of documents:

```bash
docker compose run --rm -v "$PWD/../corpus:/corpus:ro" migrate \
  python -m nursing_bot.cli ingest-dir /corpus --recursive
```

Phoenix traces: http://localhost:6006 (bound to the server itself; use an SSH tunnel).

Without a domain the app runs over plain HTTP: fine on a trusted network, and the sign-in pages
warn about it otherwise. Adding a domain later: set `SITE_ADDRESS=<domain>` and
`AUTH_COOKIE_SECURE=true`, then `docker compose up -d`.

## Local development

```bash
uv sync --extra dev --extra parsers
docker compose up -d postgres minio minio-init            # infrastructure only
uv run python -m nursing_bot.cli migrate
uv run uvicorn nursing_bot.api.main:app --reload           # API on :8000 (INGESTION_MODE=inline)

cd frontend && npm ci && npm run dev                       # app on http://localhost:5173
```

Vite proxies `/api` to `:8000`, so the browser sees one origin, as behind Caddy.

## Quality checks

```bash
# Python: lint, types, unit + integration tests (needs a disposable pgvector database; it is wiped!)
docker run -d --name nursingbot-test-pg -e POSTGRES_PASSWORD=testpass -e POSTGRES_DB=nursing_bot_test \
  -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.7-pg18-trixie
uv run ruff check src tests alembic && uv run ruff format --check src tests alembic
uv run mypy
uv run pytest --cov

# Frontend: lint, types, unit tests, build, browser end-to-end tests
cd frontend
npm run lint && npm run typecheck && npm test && npm run build
npx playwright install chromium && npm run e2e   # starts the API with fake AI providers
```

Evaluation against the real models (after loading the corpus):

```bash
uv run python -m nursing_bot.cli eval retrieval        # free: hit rate + suggested gate threshold
uv run python -m nursing_bot.cli eval answer --judge   # costs tokens: answers, routing, leaks
```

## Project layout

```
src/nursing_bot/
  config.py           every setting, with production safety checks
  privacy.py          PII redaction (guardrails, history, tracing)
  usage.py            daily token quotas and the global spending cap
  accounts/           Argon2id passwords, cookie sessions, admin-assisted resets
  api/                FastAPI app, auth and CSRF, routes (accounts, conversations, guest chat, admin)
  generation/         guardrails, condenser, answer router, official-facts check, verifier, pipeline
  retrieval/          dense, sparse, fusion, rerank, gate, retriever
  ingestion/          loaders, splitter, context headers, tabular cards, pipeline
  evaluation/         dataset, metrics, LLM judge, runner
  models/             LiteLLM client (the only module that calls AI providers)
  storage/            ORM models, database, S3
  observability/      logging, OpenTelemetry → Phoenix
  workers/            Procrastinate tasks
  jobs.py, cli.py     background jobs; migrate | ingest-dir | eval | create-user | reset-password ...
frontend/             React + Vite + TypeScript app (chat, accounts, settings, admin), Playwright e2e
alembic/              database migrations
eval/                 evaluation set (77 cases)
scripts/              backup, restore, restore test, eval gate
```
